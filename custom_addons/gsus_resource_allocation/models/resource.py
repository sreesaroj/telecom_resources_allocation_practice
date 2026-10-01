from odoo import api, fields, models
from odoo.exceptions import ValidationError


class GsusZone(models.Model):
    _name = "gsus.zone"
    _description = "GSUS Zone"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "sequence, name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(required=False, tracking=True)
    sequence = fields.Integer(default=10)
    manager_id = fields.Many2one("res.users", string="Zone Manager", tracking=True)
    project_id = fields.Many2one("project.project", tracking=True)
    customer_id = fields.Many2one(related="project_id.customer_id", store=True)
    location_ids = fields.One2many("gsus.location", "zone_id")
    active = fields.Boolean(default=True)
    _code_unique = models.Constraint("UNIQUE(code)", "Zone code must be unique.")


class GsusLocation(models.Model):
    _name = "gsus.location"
    _description = "GSUS FRT Location"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "zone_id, sequence, name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(required=False, tracking=True)
    sequence = fields.Integer(default=10)
    zone_id = fields.Many2one("gsus.zone", required=True, ondelete="restrict", tracking=True)
    project_id = fields.Many2one(related="zone_id.project_id", store=True)
    customer_id = fields.Many2one(related="zone_id.customer_id", store=True)
    district_id = fields.Many2one("gsus.district", domain="[('zone_id', '=', zone_id)]", tracking=True)
    service_category_ids = fields.Many2many(
        "gsus.service.category", "gsus_location_service_category_rel",
        "location_id", "category_id", string="Supported Services",
    )
    circle = fields.Char(default="TN", tracking=True)
    frt_hours = fields.Selection([("12", "12 Hours"), ("24", "24 Hours")], tracking=True)
    monthly_km_scope = fields.Float(
        string="Monthly KM Scope", digits=(16, 2), tracking=True,
        help="Monthly vehicle KM target for this FRT Location.",
    )
    address = fields.Text()
    latitude = fields.Float(digits=(10, 7))
    longitude = fields.Float(digits=(10, 7))
    responsible_id = fields.Many2one("res.users", string="Responsible Person", tracking=True)
    rtm_manager_id = fields.Many2one(
        "res.users", string="RTM Location Manager", tracking=True,
        help="This RTM Manager can view, create and edit records for this FRT Location only.",
    )
    driver_resource_id = fields.Many2one(
        "gsus.resource", string="Driver",
        domain="[('active', '=', True), ('resource_type_id.code', '=', 'DRIVER')]",
        tracking=True,
        help="Only active Person Master records categorized as Driver can be selected.",
    )
    portal_user_ids = fields.Many2many(
        "res.users", "gsus_location_portal_user_rel", "location_id", "user_id",
        string="Portal Users", domain=[("share", "=", True)],
    )
    requirement_ids = fields.One2many("gsus.requirement", "location_id")
    assignment_ids = fields.One2many("gsus.assignment", "location_id")
    active = fields.Boolean(default=True)
    _code_unique = models.Constraint("UNIQUE(code)", "FRT code must be unique.")

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
        """Bring existing FRT location managers into the restricted RTM role on upgrade."""
        group = self.env.ref("gsus_resource_allocation.group_gsus_rtm_manager", raise_if_not_found=False)
        if group:
            self.env.cr.execute(
                """INSERT INTO res_groups_users_rel (gid, uid)
                   SELECT %s, rtm_manager_id FROM gsus_location
                    WHERE rtm_manager_id IS NOT NULL
                   ON CONFLICT DO NOTHING""",
                [group.id],
            )

    @api.constrains("driver_resource_id")
    def _check_driver_resource_type(self):
        for location in self:
            driver = location.driver_resource_id
            if driver and (
                not driver.active
                or (driver.resource_type_id.code or "").upper() != "DRIVER"
            ):
                raise ValidationError("FRT Location Driver must be an active Person Master record with Resource Type Driver.")


class GsusResourceType(models.Model):
    _name = "gsus.resource.type"
    _description = "GSUS Resource Type"
    _order = "sequence, name"

    name = fields.Char(required=True)
    code = fields.Char(required=False)
    customer_id = fields.Many2one("res.partner", domain=[("is_telecom_customer", "=", True)], string="Customer")
    project_id = fields.Many2one("project.project", domain="[('customer_id', '=', customer_id)]", string="Project")
    sequence = fields.Integer(default=10)
    category = fields.Selection([
        ("person", "Employee"), ("vehicle", "Vehicle"),
        ("equipment", "Equipment"), ("generic", "Generic"),
    ], required=True, default="person", string="Resource Source")
    allow_temporary = fields.Boolean(default=True)
    color = fields.Integer()
    active = fields.Boolean(default=True)
    _code_unique = models.Constraint("UNIQUE(code)", "Resource type code must be unique.")

    @api.onchange("project_id")
    def _onchange_project_customer(self):
        if self.project_id:
            self.customer_id = self.project_id.customer_id


class GsusWorker(models.Model):
    _name = "gsus.worker"
    _description = "GSUS Worker / Resource"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    employee_code = fields.Char(tracking=True)
    mobile = fields.Char()
    resource_type_id = fields.Many2one("gsus.resource.type", required=True, domain=[("category", "=", "person")], tracking=True)
    employment_type = fields.Selection([("permanent", "Permanent"), ("temporary", "Temporary")], default="permanent", required=True, tracking=True)
    vendor_name = fields.Char()
    joining_date = fields.Date()
    active = fields.Boolean(default=True)
    notes = fields.Text()
    assignment_ids = fields.One2many("gsus.assignment", "worker_id")
    current_location_id = fields.Many2one("gsus.location", compute="_compute_current_assignment", store=False)

    def _compute_current_assignment(self):
        today = fields.Date.context_today(self)
        for worker in self:
            assignment = worker.assignment_ids.filtered(
                lambda a: a.state == "active" and a.start_date <= today and (not a.end_date or a.end_date >= today)
            )[:1]
            worker.current_location_id = assignment.location_id


class GsusRequirement(models.Model):
    _name = "gsus.requirement"
    _description = "FRT Resource Requirement"
    _order = "location_id, resource_type_id"

    location_id = fields.Many2one("gsus.location", required=True, ondelete="cascade")
    zone_id = fields.Many2one(related="location_id.zone_id", store=True)
    project_id = fields.Many2one(related="location_id.project_id", store=True)
    customer_id = fields.Many2one(related="location_id.customer_id", store=True)
    service_category_id = fields.Many2one("gsus.service.category", string="Service Category")
    shift_id = fields.Many2one("gsus.shift")
    resource_type_id = fields.Many2one("gsus.resource.type", required=True, ondelete="restrict")
    resource_category = fields.Selection(related="resource_type_id.category")
    assigned_resource_ids = fields.Many2many(
        "gsus.resource", "gsus_requirement_resource_rel", "requirement_id", "resource_id",
        string="Assigned Employees",
        domain="[('resource_type_id', '=', resource_type_id), ('resource_source', '!=', 'vehicle'), ('active', '=', True)]",
        help="Active persons from the common Person Master, matching the selected resource type.",
    )
    assigned_vehicle_ids = fields.Many2many(
        "fleet.vehicle", "gsus_requirement_vehicle_rel", "requirement_id", "vehicle_id",
        string="Assigned Vehicles",
        domain="[('active', '=', True)]",
        help="Active vehicles from the common Vehicle Master.",
    )
    required_count = fields.Integer(required=True, default=1)
    notes = fields.Char()
    active = fields.Boolean(default=True)
    actual_available_qty = fields.Integer(compute="_compute_actual_quantities", string="Available Units")
    reserved_qty = fields.Integer(compute="_compute_actual_quantities")
    allocated_qty = fields.Integer(compute="_compute_actual_quantities")
    actual_gap_qty = fields.Integer(compute="_compute_actual_quantities", string="Actual Gap")
    _location_resource_unique = models.Constraint(
        "UNIQUE(location_id, service_category_id, resource_type_id, shift_id)", "Only one scope line is allowed per FRT, service, resource type and shift."
    )

    @api.constrains("required_count")
    def _check_required_count(self):
        if any(record.required_count < 0 for record in self):
            raise ValidationError("Required count cannot be negative.")

    @api.constrains("assigned_resource_ids", "assigned_vehicle_ids", "location_id", "resource_type_id")
    def _check_unique_frt_assignments(self):
        """A regular FRT scope assignment belongs to one active FRT only."""
        for record in self:
            wrong_type = record.assigned_resource_ids.filtered(
                lambda resource: resource.resource_type_id != record.resource_type_id
            )
            if wrong_type:
                raise ValidationError(
                    "%s does not match the selected Resource Type (%s)." % (
                        ", ".join(wrong_type.mapped("name")), record.resource_type_id.name,
                    )
                )
            other_requirements = self.search([
                ("id", "not in", record.ids),
                ("location_id", "!=", record.location_id.id),
                ("location_id.active", "=", True),
            ])
            duplicate_people = record.assigned_resource_ids & other_requirements.mapped("assigned_resource_ids")
            if duplicate_people:
                locations = ", ".join(sorted(set(other_requirements.filtered(
                    lambda item: item.assigned_resource_ids & duplicate_people
                ).mapped("location_id.name"))))
                raise ValidationError(
                    "%s is already assigned to %s. Remove the person from that FRT before assigning here." % (
                        ", ".join(duplicate_people.mapped("name")), locations,
                    )
                )
            duplicate_vehicles = record.assigned_vehicle_ids & other_requirements.mapped("assigned_vehicle_ids")
            if duplicate_vehicles:
                locations = ", ".join(sorted(set(other_requirements.filtered(
                    lambda item: item.assigned_vehicle_ids & duplicate_vehicles
                ).mapped("location_id.name"))))
                raise ValidationError(
                    "%s is already assigned to %s. Remove the vehicle from that FRT before assigning here." % (
                        ", ".join(duplicate_vehicles.mapped("license_plate")), locations,
                    )
                )

    def _compute_actual_quantities(self):
        Resource = self.env["gsus.resource"]
        for record in self:
            domain = [("location_id", "=", record.location_id.id), ("resource_type_id", "=", record.resource_type_id.id)]
            if record.service_category_id:
                domain.append(("service_category_id", "=", record.service_category_id.id))
            if record.shift_id:
                domain.append(("shift_id", "=", record.shift_id.id))
            grouped = Resource._read_group(domain, ["status"], ["__count"])
            counts = {status: count for status, count in grouped}
            record.actual_available_qty = counts.get("available", 0)
            record.reserved_qty = counts.get("reserved", 0)
            record.allocated_qty = counts.get("allocated", 0) + counts.get("deployed", 0)
            covered = record.actual_available_qty + record.reserved_qty + record.allocated_qty
            record.actual_gap_qty = max(record.required_count - covered, 0)


class GsusAssignment(models.Model):
    _name = "gsus.assignment"
    _description = "GSUS Resource Assignment"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "start_date desc, id desc"

    name = fields.Char(default="New", copy=False, readonly=True)
    worker_id = fields.Many2one("gsus.worker", required=True, ondelete="restrict", tracking=True)
    resource_type_id = fields.Many2one(related="worker_id.resource_type_id", store=True)
    location_id = fields.Many2one("gsus.location", required=True, ondelete="restrict", tracking=True)
    zone_id = fields.Many2one(related="location_id.zone_id", store=True)
    start_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    end_date = fields.Date(tracking=True)
    shift = fields.Selection([("day", "Day"), ("night", "Night"), ("both", "Both")], default="both", required=True)
    state = fields.Selection([("draft", "Draft"), ("active", "Active"), ("ended", "Ended")], default="draft", tracking=True)
    notes = fields.Char()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("gsus.assignment") or "New"
        return super().create(vals_list)

    @api.constrains("worker_id", "start_date", "end_date", "state")
    def _check_dates_and_overlap(self):
        for record in self:
            if record.end_date and record.end_date < record.start_date:
                raise ValidationError("End date cannot be earlier than start date.")
            if record.state != "active":
                continue
            domain = [("id", "!=", record.id), ("worker_id", "=", record.worker_id.id), ("state", "=", "active")]
            for other in self.search(domain):
                if (not record.end_date or other.start_date <= record.end_date) and (not other.end_date or record.start_date <= other.end_date):
                    raise ValidationError("This worker already has an overlapping active assignment.")

    def action_activate(self):
        self.write({"state": "active"})

    def action_end(self):
        self.write({"state": "ended", "end_date": fields.Date.context_today(self)})
