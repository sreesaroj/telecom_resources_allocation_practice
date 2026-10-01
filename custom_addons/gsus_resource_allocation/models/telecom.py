from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    is_telecom_customer = fields.Boolean(string="Telecom Customer")
    telecom_portal_enabled = fields.Boolean()
    allowed_project_ids = fields.Many2many(
        "project.project", "gsus_partner_project_rel", "partner_id", "project_id",
        string="Allowed Telecom Projects",
    )
    telecom_portal_user_ids = fields.Many2many(
        "res.users", "gsus_customer_portal_user_rel", "customer_id", "user_id",
        string="GSUS Portal Users", domain=[("share", "=", True)],
        help="Selected portal users can view only this telecom customer's GSUS dashboard and Excel report.",
    )


class ProjectProject(models.Model):
    _inherit = "project.project"

    gsus_location_ids = fields.One2many(
        "gsus.location", "project_id", string="GSUS FRT Locations",
    )
    rtm_manager_id = fields.Many2one(
        "res.users", string="RTM Project Manager", tracking=True,
        help="This RTM Manager can view and maintain records for this project only.",
    )
    customer_id = fields.Many2one("res.partner", domain=[("is_telecom_customer", "=", True)], tracking=True)
    telecom_project_type = fields.Selection([
        ("frt_maintenance", "FRT Maintenance"), ("mobility", "Mobility"),
        ("utility", "Utility"), ("other", "Other"),
    ], default="frt_maintenance", tracking=True)
    telecom_active = fields.Boolean(default=True)
    gsus_hour_format = fields.Selection([
        ("12", "12 Hours"),
        ("24", "24 Hours"),
    ], string="Operating Hour Format", default="12", required=True, tracking=True)
    service_category_ids = fields.Many2many(
        "gsus.service.category", "gsus_project_service_category_rel",
        "project_id", "category_id", string="Service Categories",
    )

    def _ensure_rtm_manager_group(self):
        group = self.env.ref("gsus_resource_allocation.group_gsus_rtm_manager", raise_if_not_found=False)
        managers = self.mapped("rtm_manager_id")
        if group and managers:
            managers.sudo().write({"group_ids": [(4, group.id)]})

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._ensure_rtm_manager_group()
        return records

    def write(self, vals):
        result = super().write(vals)
        if "rtm_manager_id" in vals:
            self._ensure_rtm_manager_group()
        return result

    def init(self):
        """Bring existing project managers into the restricted RTM role on upgrade."""
        group = self.env.ref("gsus_resource_allocation.group_gsus_rtm_manager", raise_if_not_found=False)
        if group:
            self.env.cr.execute(
                """INSERT INTO res_groups_users_rel (gid, uid)
                   SELECT %s, rtm_manager_id FROM project_project
                    WHERE rtm_manager_id IS NOT NULL
                   ON CONFLICT DO NOTHING""",
                [group.id],
            )


class GsusServiceCategory(models.Model):
    _name = "gsus.service.category"
    _description = "GSUS Service Category"
    _order = "sequence, name"

    name = fields.Char(required=True)
    code = fields.Char(required=False)
    description = fields.Text()
    sequence = fields.Integer(default=10)
    icon = fields.Selection([
        ("frt", "FRT"), ("patroller", "Patroller"),
        ("mobility", "Mobility"), ("route", "Route Cause"),
        ("utility", "Utility"),
    ], default="frt")
    resource_type_ids = fields.Many2many(
        "gsus.resource.type", "gsus_category_resource_type_rel",
        "category_id", "resource_type_id", string="Allowed Resource Types",
    )
    active = fields.Boolean(default=True)
    _code_unique = models.Constraint("UNIQUE(code)", "Service category code must be unique.")


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    telecom_resource_id = fields.Many2one("gsus.resource", readonly=True, copy=False)


class FleetVehicle(models.Model):
    _inherit = "fleet.vehicle"

    telecom_resource_id = fields.Many2one("gsus.resource", readonly=True, copy=False)
    gsus_vehicle_type = fields.Selection([
        ("gsus", "GSUS"),
        ("vendor", "Vendor"),
    ], string="Vehicle Type", default="gsus", required=True, tracking=True)
    gsus_vendor_id = fields.Many2one(
        "res.partner", string="Vendor", tracking=True,
        help="Select an existing vendor contact or create a new vendor.",
    )
    gsus_make_model = fields.Char(string="Make / Model", tracking=True)
    gsus_customer_id = fields.Many2one("res.partner", domain=[("is_telecom_customer", "=", True)], string="Telecom Customer")
    gsus_project_id = fields.Many2one("project.project", domain="[('customer_id', '=', gsus_customer_id)]", string="Telecom Project")
    gsus_zone_id = fields.Many2one("gsus.zone", domain="[('project_id', '=', gsus_project_id)]", string="Zone")
    gsus_location_id = fields.Many2one("gsus.location", domain="[('zone_id', '=', gsus_zone_id)]", string="FRT")
    gsus_status = fields.Selection([
        ("available", "Available"), ("unavailable", "Unavailable"),
        ("maintenance", "Maintenance"), ("expired", "Insurance Expired"),
    ], default="available", required=True, string="Availability Status", tracking=True)
    insurance_policy_no = fields.Char(tracking=True)
    insurance_company = fields.Char(tracking=True)
    insurance_expiry_date = fields.Date(tracking=True)
    fc_date = fields.Date(string="FC Date", tracking=True)
    permit_expiry_date = fields.Date(tracking=True)
    pollution_expiry_date = fields.Date(tracking=True)
    insurance_document_ids = fields.Many2many(
        "ir.attachment", "fleet_vehicle_gsus_insurance_attachment_rel",
        "vehicle_id", "attachment_id", string="Insurance Documents",
    )
    vehicle_document_ids = fields.Many2many(
        "ir.attachment", "fleet_vehicle_gsus_document_rel",
        "vehicle_id", "attachment_id", string="Other Vehicle Documents",
    )
    gsus_driver_contact_ids = fields.Many2many(
        "res.partner", compute="_compute_gsus_driver_contact_ids",
        string="Eligible Drivers",
    )

    def _driver_resources(self):
        return self.env["gsus.resource"].search([
            ("active", "=", True),
            ("employee_id", "!=", False),
            "|", ("resource_type_id.code", "=", "DRIVER"),
            ("resource_type_id.name", "ilike", "driver"),
        ])

    def _compute_gsus_driver_contact_ids(self):
        driver_contacts = self._driver_resources().mapped("employee_id.work_contact_id")
        for vehicle in self:
            vehicle.gsus_driver_contact_ids = driver_contacts

    @api.constrains("driver_id")
    def _check_driver_is_driver_category(self):
        driver_resources = self._driver_resources()
        eligible_contacts = driver_resources.mapped("employee_id.work_contact_id")
        for vehicle in self:
            if vehicle.driver_id and vehicle.driver_id not in eligible_contacts:
                raise ValidationError("Vehicle Driver must be an active Person Master record with Resource Type Driver.")

    @api.onchange("gsus_vehicle_type")
    def _onchange_gsus_vehicle_type(self):
        if self.gsus_vehicle_type != "vendor":
            self.gsus_vendor_id = False

    @api.constrains("gsus_vehicle_type", "gsus_vendor_id")
    def _check_vendor_vehicle(self):
        for vehicle in self:
            if vehicle.gsus_vehicle_type == "vendor" and not vehicle.gsus_vendor_id:
                raise ValidationError("Select a Vendor when Vehicle Type is Vendor.")

    @api.depends("license_plate")
    def _compute_vehicle_name(self):
        for record in self:
            record.name = record.license_plate or "New Vehicle"

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if "model_id" in fields_list and not values.get("model_id"):
            brand = self.env["fleet.vehicle.model.brand"].search([("name", "=", "GSUS")], limit=1)
            if not brand:
                brand = self.env["fleet.vehicle.model.brand"].create({"name": "GSUS"})
            model = self.env["fleet.vehicle.model"].search([
                ("name", "=", "Operational Vehicle"), ("brand_id", "=", brand.id)
            ], limit=1)
            if not model:
                model = self.env["fleet.vehicle.model"].create({"name": "Operational Vehicle", "brand_id": brand.id})
            values["model_id"] = model.id
        return values

    @api.model_create_multi
    def create(self, vals_list):
        generic_model = False
        if any(not vals.get("model_id") for vals in vals_list):
            brand = self.env["fleet.vehicle.model.brand"].search([("name", "=", "GSUS")], limit=1)
            if not brand:
                brand = self.env["fleet.vehicle.model.brand"].create({"name": "GSUS"})
            generic_model = self.env["fleet.vehicle.model"].search([
                ("name", "=", "Operational Vehicle"), ("brand_id", "=", brand.id)
            ], limit=1)
            if not generic_model:
                generic_model = self.env["fleet.vehicle.model"].create({
                    "name": "Operational Vehicle", "brand_id": brand.id,
                })
        for vals in vals_list:
            if not vals.get("model_id"):
                vals["model_id"] = generic_model.id
        return super().create(vals_list)

    @api.onchange("gsus_customer_id")
    def _onchange_gsus_customer(self):
        self.gsus_project_id = False
        self.gsus_zone_id = False
        self.gsus_location_id = False
        projects = self.env["project.project"].search([("customer_id", "=", self.gsus_customer_id.id), ("telecom_active", "=", True)])
        if len(projects) == 1:
            self.gsus_project_id = projects

    @api.onchange("gsus_project_id")
    def _onchange_gsus_project(self):
        self.gsus_zone_id = False
        self.gsus_location_id = False
        zones = self.env["gsus.zone"].search([("project_id", "=", self.gsus_project_id.id)])
        if len(zones) == 1:
            self.gsus_zone_id = zones

    @api.onchange("gsus_zone_id")
    def _onchange_gsus_zone(self):
        self.gsus_location_id = False
        locations = self.env["gsus.location"].search([("zone_id", "=", self.gsus_zone_id.id)])
        if len(locations) == 1:
            self.gsus_location_id = locations


class GsusShift(models.Model):
    _name = "gsus.shift"
    _description = "GSUS Operational Shift"
    _order = "sequence, name"

    name = fields.Char(required=True)
    code = fields.Char(required=False)
    hours = fields.Float(required=True, default=12)
    start_time = fields.Float(string="Start Time", required=True, default=9.0)
    end_time = fields.Float(string="End Time", required=True, default=21.0)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    _code_unique = models.Constraint("UNIQUE(code)", "Shift code must be unique.")

    @api.constrains("start_time", "end_time")
    def _check_shift_times(self):
        for shift in self:
            if not (0 <= shift.start_time < 24 and 0 <= shift.end_time < 24):
                raise ValidationError("Shift Start Time and End Time must be between 00:00 and 23:59.")

    def init(self):
        """Set sensible times for the existing standard shifts once after this feature upgrade."""
        parameters = self.env["ir.config_parameter"].sudo()
        if parameters.get_param("gsus.shift_times_migrated"):
            return
        defaults = {"DAY12": (9.0, 21.0), "NIGHT12": (21.0, 9.0), "FULL24": (9.0, 9.0)}
        for code, (start_time, end_time) in defaults.items():
            shift = self.search([("code", "=", code)], limit=1)
            if shift:
                shift.write({"start_time": start_time, "end_time": end_time})
        parameters.set_param("gsus.shift_times_migrated", "1")


class GsusDistrict(models.Model):
    _name = "gsus.district"
    _description = "GSUS Operational District"
    _order = "zone_id, name"

    name = fields.Char(required=True)
    code = fields.Char(required=False)
    zone_id = fields.Many2one("gsus.zone", required=True, ondelete="cascade")
    project_id = fields.Many2one(related="zone_id.project_id", store=True)
    customer_id = fields.Many2one(related="zone_id.customer_id", store=True)
    circle = fields.Char(default="TN")
    active = fields.Boolean(default=True)
    _district_code_unique = models.Constraint("UNIQUE(code)", "District code must be unique.")


class GsusResource(models.Model):
    _name = "gsus.resource"
    _description = "GSUS Actual Resource"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "resource_type_id, name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(required=False, default="New", tracking=True)
    resource_type_id = fields.Many2one("gsus.resource.type", required=True, ondelete="restrict", tracking=True)
    resource_source = fields.Selection(related="resource_type_id.category", store=True)
    is_driver = fields.Boolean(compute="_compute_is_driver")
    employee_id = fields.Many2one("hr.employee", ondelete="restrict", tracking=True)
    vehicle_id = fields.Many2one("fleet.vehicle", ondelete="restrict", tracking=True)
    old_employee_id = fields.Char(tracking=True)
    new_employee_id = fields.Char(tracking=True)
    ownership = fields.Selection([
        ("gsus_own", "GSUS Own"), ("vendor", "Vendor"),
        ("own_bike", "Own Bike FRT"), ("not_specified", "Not Specified"),
    ], default="not_specified", required=False, tracking=True)
    vendor_id = fields.Many2one("res.partner", tracking=True)
    customer_id = fields.Many2one("res.partner", required=False, domain=[("is_telecom_customer", "=", True)], tracking=True)
    project_id = fields.Many2one("project.project", required=False, domain="[('customer_id', '=', customer_id)]", tracking=True)
    service_category_id = fields.Many2one(
        "gsus.service.category", string="Service Category", tracking=True,
        help="Business service such as FRT, Patroller or Mobility. This is separate from the FRT location.",
    )
    zone_id = fields.Many2one("gsus.zone", required=False, domain="[('project_id', '=', project_id)]", tracking=True)
    location_id = fields.Many2one("gsus.location", required=False, domain="[('zone_id', '=', zone_id)]", tracking=True)
    shift_id = fields.Many2one(
        "gsus.shift", tracking=True,
        default=lambda self: self.env["gsus.shift"].search([("code", "=", "FULL24")], limit=1),
    )
    employment_type = fields.Selection([
        ("permanent", "Permanent"), ("temporary", "Temporary"), ("contract", "Contract"),
    ], default="permanent", required=False)
    status = fields.Selection([
        ("available", "Available"), ("reserved", "Reserved"), ("allocated", "Allocated"),
        ("deployed", "Deployed"), ("unavailable", "Unavailable"), ("leave", "On Leave"),
        ("maintenance", "Maintenance"), ("expired", "Expired"),
    ], default="available", required=False, tracking=True)
    personal_phone = fields.Char(string="Personal Phone", tracking=True)
    work_phone = fields.Char(string="Work Phone", tracking=True)
    personal_email = fields.Char(string="Personal Email", tracking=True)
    work_email = fields.Char(string="Work Email", tracking=True)
    date_of_birth = fields.Date(string="Date of Birth", tracking=True)
    joining_date = fields.Date(string="Joining Date", tracking=True)
    gender = fields.Selection([
        ("male", "Male"), ("female", "Female"), ("other", "Other"),
    ], tracking=True)
    identification_no = fields.Char(string="Identification / Aadhaar No.", tracking=True)
    driving_license_number = fields.Char(
        string="Driving Licence Number",
        related="employee_id.permit_no",
        readonly=False,
    )
    driving_license_expiry_date = fields.Date(
        string="Driving Licence Expiry Date",
        related="employee_id.work_permit_expiration_date",
        readonly=False,
    )
    emergency_contact = fields.Char(tracking=True)
    emergency_phone = fields.Char(tracking=True)
    private_address = fields.Char(string="Address", tracking=True)
    bank_account_no = fields.Char(string="Bank Account Number", tracking=True)
    bank_account_holder_name = fields.Char(string="Bank Account Holder Name", tracking=True)
    bank_name = fields.Char(tracking=True)
    bank_branch = fields.Char(string="Bank Branch", tracking=True)
    bank_address = fields.Text(string="Bank Address", tracking=True)
    bank_ifsc = fields.Char(string="IFSC Code", tracking=True)
    aadhaar_attachment_ids = fields.Many2many(
        "ir.attachment", "gsus_resource_aadhaar_attachment_rel",
        "resource_id", "attachment_id", string="Aadhaar Card",
    )
    bank_passbook_attachment_ids = fields.Many2many(
        "ir.attachment", "gsus_resource_bank_passbook_attachment_rel",
        "resource_id", "attachment_id", string="Bank Passbook",
    )
    driving_license_attachment_ids = fields.Many2many(
        "ir.attachment", "gsus_resource_driving_license_attachment_rel",
        "resource_id", "attachment_id", string="Driving Licence",
    )
    available_from = fields.Datetime()
    available_until = fields.Datetime()
    reserved_request_id = fields.Many2one("gsus.resource.request", readonly=True, copy=False)
    remarks = fields.Text()
    active = fields.Boolean(default=True)
    _code_unique = models.Constraint("UNIQUE(code)", "Resource code must be unique.")

    @api.depends("resource_type_id.code", "resource_type_id.name")
    def _compute_is_driver(self):
        for resource in self:
            code = (resource.resource_type_id.code or "").strip().upper()
            name = (resource.resource_type_id.name or "").strip().upper()
            resource.is_driver = code == "DRIVER" or name == "DRIVER"

    def _prepare_employee_values(self):
        self.ensure_one()
        return {
            "name": self.name or self.code or "New Person",
            "active": self.active,
            "mobile_phone": self.personal_phone or False,
            "work_phone": self.work_phone or False,
            "private_email": self.personal_email or False,
            "work_email": self.work_email or False,
            "birthday": self.date_of_birth or False,
            "contract_date_start": self.joining_date or False,
            "sex": self.gender or False,
            "identification_id": self.identification_no or False,
            "permit_no": self.driving_license_number or False,
            "work_permit_expiration_date": self.driving_license_expiry_date or False,
            "emergency_contact": self.emergency_contact or False,
            "emergency_phone": self.emergency_phone or False,
            "private_street": self.private_address or False,
        }

    def _sync_employee_details(self):
        for resource in self.filtered(lambda item: item.resource_source == "person"):
            employee = resource.employee_id.sudo()
            if not employee:
                employee = self.env["hr.employee"].sudo().create(resource._prepare_employee_values())
                resource.with_context(skip_employee_sync=True).employee_id = employee
            else:
                employee.write(resource._prepare_employee_values())
            if employee.telecom_resource_id != resource:
                employee.telecom_resource_id = resource

            # Keep the uploaded person documents on the linked Employee too,
            # so they are available from the Employee attachment panel.
            documents = (
                resource.aadhaar_attachment_ids
                | resource.bank_passbook_attachment_ids
                | resource.driving_license_attachment_ids
            )
            if documents:
                documents.sudo().write({
                    "res_model": "hr.employee",
                    "res_id": employee.id,
                })

            if resource.bank_account_no:
                bank = self.env["res.bank"].sudo().search([
                    ("bic", "=", resource.bank_ifsc),
                ], limit=1) if resource.bank_ifsc else self.env["res.bank"]
                if not bank and (resource.bank_name or resource.bank_ifsc):
                    bank = self.env["res.bank"].sudo().create({
                        "name": resource.bank_name or resource.bank_ifsc,
                        "bic": resource.bank_ifsc or False,
                    })
                account = self.env["res.partner.bank"].sudo().search([
                    ("acc_number", "=", resource.bank_account_no),
                ], limit=1)
                if not account:
                    account = self.env["res.partner.bank"].sudo().create({
                        "acc_number": resource.bank_account_no,
                        "partner_id": employee.work_contact_id.id,
                        "bank_id": bank.id or False,
                    })
                employee.bank_account_ids = [(4, account.id)]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("code") or vals.get("code") == "New":
                vals["code"] = self.env["ir.sequence"].next_by_code("gsus.resource") or "New"
            resource_type = self.env["gsus.resource.type"].browse(vals.get("resource_type_id"))
            if resource_type.category == "person" and not vals.get("employee_id"):
                employee = self.env["hr.employee"].sudo().create({"name": vals["name"]})
                vals["employee_id"] = employee.id
        records = super().create(vals_list)
        records._sync_employee_details()
        return records

    def write(self, vals):
        if vals.get("active") is False:
            self._check_can_deactivate_person()
        result = super().write(vals)
        employee_fields = {
            "name", "employee_id", "personal_phone", "work_phone",
            "personal_email", "work_email", "date_of_birth", "joining_date",
            "gender", "identification_no", "emergency_contact", "emergency_phone",
            "driving_license_number", "driving_license_expiry_date",
            "private_address", "bank_account_no", "bank_account_holder_name",
            "bank_name", "bank_branch", "bank_address", "bank_ifsc",
            "aadhaar_attachment_ids", "bank_passbook_attachment_ids",
            "driving_license_attachment_ids", "active",
        }
        if not self.env.context.get("skip_employee_sync") and employee_fields.intersection(vals):
            self._sync_employee_details()
        return result

    def _check_can_deactivate_person(self):
        """Block deactivation until FRT scope and today's duty allocations are cleared."""
        people = self.filtered(lambda resource: resource.active and resource.resource_source == "person")
        if not people:
            return
        Requirement = self.env["gsus.requirement"].sudo()
        Location = self.env["gsus.location"].sudo()
        DailyLine = self.env["gsus.frt.availability.line"].sudo()
        today = fields.Date.context_today(self)
        for person in people:
            assignments = []
            requirements = Requirement.search([("assigned_resource_ids", "in", person.id)])
            assignments += ["FRT Location %s (%s)" % (line.location_id.name, line.resource_type_id.name) for line in requirements]
            driver_locations = Location.search([("driver_resource_id", "=", person.id)])
            assignments += ["FRT Location %s (Dedicated Driver)" % location.name for location in driver_locations]
            daily_lines = DailyLine.search([
                ("availability_id.availability_date", "=", today),
                "|",
                ("available_resource_ids", "in", person.id),
                ("alternate_resource_ids", "in", person.id),
            ])
            assignments += [
                "Daily Activity %s - %s" % (
                    line.location_id.name, fields.Date.to_string(line.availability_id.availability_date),
                )
                for line in daily_lines
            ]
            if assignments:
                raise ValidationError(
                    "%s is still assigned in: %s. Please remove the employee from these FRT Location / Daily Activity records, then deactivate." % (
                        person.name, "; ".join(assignments),
                    )
                )

    def _remove_inactive_person_from_operational_assignments(self):
        """Remove a deactivated person from FRT scope and today's duty sheet."""
        people = self.filtered(lambda resource: resource.resource_source == "person" and not resource.active)
        if not people:
            return

        Requirement = self.env["gsus.requirement"].sudo()
        Location = self.env["gsus.location"].sudo()
        DailyLine = self.env["gsus.frt.availability.line"].sudo()
        today = fields.Date.context_today(self)
        people_ids = people.ids
        scope_details = {person.id: [] for person in people}
        daily_details = {person.id: [] for person in people}
        affected_locations = self.env["gsus.location"]
        affected_sheets = self.env["gsus.frt.availability"]

        # Permanent FRT scope and the dedicated driver field are both assignments.
        requirements = Requirement.search([("assigned_resource_ids", "in", people_ids)])
        for requirement in requirements:
            removed = requirement.assigned_resource_ids.filtered(lambda person: person.id in people_ids)
            if removed:
                requirement.write({"assigned_resource_ids": [(3, person.id) for person in removed]})
                affected_locations |= requirement.location_id
                detail = "%s / %s" % (requirement.location_id.name, requirement.resource_type_id.name)
                for person in removed:
                    scope_details[person.id].append(detail)
        for location in Location.search([("driver_resource_id", "in", people_ids)]):
            person = location.driver_resource_id
            location.write({"driver_resource_id": False})
            affected_locations |= location
            scope_details[person.id].append("%s / Dedicated Driver" % location.name)

        # Only today's daily activity is changed. Already-created attendance stays intact.
        daily_lines = DailyLine.search([
            ("availability_id.availability_date", "=", today),
            "|", "|",
            ("available_resource_ids", "in", people_ids),
            ("alternate_resource_ids", "in", people_ids),
            ("unavailable_resource_ids", "in", people_ids),
        ])
        for line in daily_lines:
            regular_removed = line.available_resource_ids.filtered(lambda person: person.id in people_ids)
            alternate_removed = line.alternate_resource_ids.filtered(lambda person: person.id in people_ids)
            unavailable_removed = line.unavailable_resource_ids.filtered(lambda person: person.id in people_ids)
            removed = regular_removed | alternate_removed | unavailable_removed
            if not removed:
                continue
            remaining_regular = line.available_resource_ids - removed
            remaining_alternate = line.alternate_resource_ids - removed
            line.with_context(skip_unavailable_sync=True).write({
                "available_resource_ids": [(6, 0, remaining_regular.ids)],
                "alternate_resource_ids": [(6, 0, remaining_alternate.ids)],
                "unavailable_resource_ids": [(6, 0, (line.unavailable_resource_ids - removed).ids)],
                "available_count": len(remaining_regular),
                "alternate_count": len(remaining_alternate),
            })
            affected_locations |= line.location_id
            affected_sheets |= line.availability_id
            for person in removed:
                roles = []
                if person in regular_removed:
                    roles.append("Regular")
                if person in alternate_removed:
                    roles.append("Alternate")
                if person in unavailable_removed:
                    roles.append("Not Available")
                daily_details[person.id].append("%s — %s (%s)" % (
                    line.location_id.name,
                    fields.Date.to_string(line.availability_id.availability_date),
                    ", ".join(roles),
                ))

        # Stored totals are recomputed immediately, so the daily sheet shows
        # the revised gap without requiring Reload FRT Scope.
        affected_sheets._compute_totals()

        # This appears in the Person Master chatter immediately after Save,
        # including the no-record case so the action is always auditable.
        for person in people:
            details = []
            if scope_details[person.id]:
                details.append("Removed from FRT Location assignment(s): %s" % ", ".join(scope_details[person.id]))
            if daily_details[person.id]:
                details.append("Removed from today's Daily Activity: %s" % ", ".join(daily_details[person.id]))
            if not daily_details[person.id]:
                details.append("No Daily Activity allocation existed for %s; historical availability records were retained." % fields.Date.to_string(today))
            person.message_post(
                body="<b>Person deactivated — automatic assignment cleanup completed.</b><br/>%s" % "<br/>".join(details),
                subtype_xmlid="mail.mt_note",
            )

            notification = {
                "title": "Employee Deactivated",
                "message": "%s was made inactive. %s" % (person.name, " ".join(details)),
                "type": "warning",
            }
            managers = affected_locations.mapped("rtm_manager_id") | affected_locations.mapped("project_id.rtm_manager_id")
            for manager in managers.filtered("partner_id"):
                self.env["bus.bus"]._sendone(
                    manager.partner_id, "gsus_persistent_notification", notification,
                )

    @api.constrains("personal_phone", "work_phone")
    def _check_phone_numbers(self):
        for resource in self:
            for phone, label in (
                (resource.personal_phone, "Mobile number"),
                (resource.work_phone, "Work phone number"),
            ):
                if phone and (not phone.isdigit() or len(phone) != 10):
                    raise ValidationError("%s must contain exactly 10 digits." % label)

    @api.constrains("resource_type_id", "personal_phone", "identification_no")
    def _check_required_person_identity(self):
        for resource in self:
            if resource.resource_source != "person":
                continue
            if not resource.personal_phone:
                raise ValidationError("Personal Phone is mandatory for every person.")
            if not resource.identification_no:
                raise ValidationError("Aadhaar Card Number is mandatory for every person.")

    @api.constrains("identification_no")
    def _check_identification_no(self):
        for resource in self:
            if resource.identification_no and (
                not resource.identification_no.isdigit() or len(resource.identification_no) != 12
            ):
                raise ValidationError("Aadhaar number must contain exactly 12 digits.")
            if resource.identification_no and self.with_context(active_test=False).sudo().search_count([
                ("identification_no", "=", resource.identification_no),
                ("id", "!=", resource.id),
            ]):
                raise ValidationError("This Aadhaar number is already assigned to another person.")

    @api.constrains("bank_ifsc")
    def _check_bank_ifsc(self):
        for resource in self:
            if resource.bank_ifsc and (
                len(resource.bank_ifsc) != 11 or not resource.bank_ifsc.isalnum()
            ):
                raise ValidationError("IFSC Code must contain exactly 11 letters or digits.")

    @api.constrains("date_of_birth")
    def _check_minimum_age(self):
        today = fields.Date.context_today(self)
        for resource in self:
            if not resource.date_of_birth:
                continue
            age = today.year - resource.date_of_birth.year - (
                (today.month, today.day)
                < (resource.date_of_birth.month, resource.date_of_birth.day)
            )
            if age < 18:
                raise ValidationError("Employee must be at least 18 years old.")

    @api.constrains("resource_source", "employee_id", "vehicle_id")
    def _check_source_link(self):
        for record in self:
            if record.resource_source != "person" and record.employee_id:
                raise ValidationError("Only Employee resource types can link an Employee.")
            if record.resource_source != "vehicle" and record.vehicle_id:
                raise ValidationError("Only Vehicle resource types can link a Fleet Vehicle.")

    @api.constrains("customer_id", "project_id", "service_category_id", "zone_id", "location_id")
    def _check_hierarchy(self):
        for record in self:
            if record.project_id and record.customer_id and record.project_id.customer_id != record.customer_id:
                raise ValidationError("The selected Project does not belong to this Customer.")
            if record.zone_id and record.project_id and record.zone_id.project_id != record.project_id:
                raise ValidationError("The selected Zone does not belong to this Project.")
            if record.location_id and record.zone_id and record.location_id.zone_id != record.zone_id:
                raise ValidationError("The selected FRT does not belong to this Zone.")
            if (record.service_category_id and record.project_id and record.project_id.service_category_ids
                    and record.service_category_id not in record.project_id.service_category_ids):
                raise ValidationError("The selected Service Category is not enabled for this Project.")

    def name_get(self):
        return [(record.id, f"[{record.code}] {record.name}") for record in self]
