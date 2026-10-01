from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class GsusFrtAvailability(models.Model):
    _name = "gsus.frt.availability"
    _description = "FRT Current Availability"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "availability_date desc, zone_id, location_id"

    name = fields.Char(compute="_compute_name", store=True)
    customer_id = fields.Many2one("res.partner", required=True, domain=[("is_telecom_customer", "=", True)], tracking=True)
    project_id = fields.Many2one("project.project", required=True, domain="[('customer_id', '=', customer_id)]", tracking=True)
    zone_id = fields.Many2one("gsus.zone", required=True, domain="[('project_id', '=', project_id)]", tracking=True)
    location_id = fields.Many2one("gsus.location", string="FRT", required=True, domain="[('zone_id', '=', zone_id)]", tracking=True)
    availability_date = fields.Date(string="Availability Date", required=True, default=fields.Date.context_today, tracking=True, index=True)
    team_check_in = fields.Datetime(string="Team Check-in Date & Time", tracking=True)
    team_check_out = fields.Datetime(string="Team Check-out Date & Time", tracking=True)
    first_shift_check_in = fields.Datetime(string="First Shift Check-in Time", tracking=True)
    first_shift_check_out = fields.Datetime(string="First Shift Check-out Time", tracking=True)
    second_shift_check_in = fields.Datetime(string="Second Shift Check-in Time", tracking=True)
    second_shift_check_out = fields.Datetime(string="Second Shift Check-out Time", tracking=True)
    as_on = fields.Datetime(string="Last Updated", default=fields.Datetime.now, readonly=True, tracking=True)
    updated_by = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)
    responsible_id = fields.Many2one("res.users", tracking=True)
    line_ids = fields.One2many("gsus.frt.availability.line", "availability_id", string="FRT Scope", copy=True)
    vehicle_km_line_ids = fields.One2many(
        "gsus.frt.vehicle.km", "availability_id", string="Vehicle KM Capture", copy=False,
    )
    alternate_allocation_ids = fields.One2many(
        "gsus.frt.alternate.allocation", "availability_id", string="Temporary Alternates", copy=False,
    )
    history_ids = fields.One2many("gsus.frt.availability.history", "availability_id", readonly=True)
    total_scope = fields.Integer(compute="_compute_totals", store=True)
    total_available = fields.Integer(compute="_compute_totals", store=True)
    total_gap = fields.Integer(compute="_compute_totals", store=True)
    readiness_percent = fields.Float(compute="_compute_totals", store=True)
    notes = fields.Text()
    active = fields.Boolean(default=True)
    _location_date_unique = models.Constraint(
        "UNIQUE(location_id, availability_date)",
        "A daily availability entry already exists for this FRT and date.",
    )

    def init(self):
        self.env.cr.execute(
            "ALTER TABLE gsus_frt_availability "
            "DROP CONSTRAINT IF EXISTS gsus_frt_availability_location_unique"
        )

    @api.depends("location_id", "project_id", "availability_date")
    def _compute_name(self):
        for record in self:
            base_name = record.location_id.name or record.project_id.name or "New FRT Availability"
            record.name = "%s - %s" % (base_name, record.availability_date) if record.availability_date else base_name

    @api.depends("line_ids.scope_count", "line_ids.total_available", "line_ids.gap_count")
    def _compute_totals(self):
        for record in self:
            record.total_scope = sum(record.line_ids.mapped("scope_count"))
            record.total_available = sum(record.line_ids.mapped("total_available"))
            record.total_gap = sum(record.line_ids.mapped("gap_count"))
            record.readiness_percent = record.total_scope and min(record.total_available / record.total_scope * 100, 100) or 100

    @api.constrains("team_check_in", "team_check_out")
    def _check_team_times(self):
        for record in self:
            if record.team_check_in and record.team_check_out and record.team_check_out < record.team_check_in:
                raise ValidationError(_("Team Check-out must be later than Team Check-in."))

    @api.constrains(
        "first_shift_check_in", "first_shift_check_out",
        "second_shift_check_in", "second_shift_check_out",
    )
    def _check_shift_times(self):
        for record in self:
            if (
                record.first_shift_check_in
                and record.first_shift_check_out
                and record.first_shift_check_out < record.first_shift_check_in
            ):
                raise ValidationError(_("First Shift Check-out must be later than First Shift Check-in."))
            if (
                record.second_shift_check_in
                and record.second_shift_check_out
                and record.second_shift_check_out < record.second_shift_check_in
            ):
                raise ValidationError(_("Second Shift Check-out must be later than Second Shift Check-in."))

    @api.onchange("project_id")
    def _onchange_project(self):
        if self.project_id:
            self.customer_id = self.project_id.customer_id

    @api.onchange("customer_id")
    def _onchange_customer(self):
        self.project_id = False
        self.zone_id = False
        self.location_id = False
        self.line_ids = [(5, 0, 0)]
        projects = self.env["project.project"].search([
            ("customer_id", "=", self.customer_id.id), ("telecom_active", "=", True)
        ])
        if len(projects) == 1:
            self.project_id = projects
            self._select_project_children()

    def _select_project_children(self):
        zones = self.env["gsus.zone"].search([("project_id", "=", self.project_id.id)])
        if len(zones) == 1:
            self.zone_id = zones
            self._select_zone_children()

    @api.onchange("project_id")
    def _onchange_project_cascade(self):
        self.zone_id = False
        self.location_id = False
        self.line_ids = [(5, 0, 0)]
        if self.project_id:
            self.customer_id = self.project_id.customer_id
            self._select_project_children()

    def _select_zone_children(self):
        locations = self.env["gsus.location"].search([("zone_id", "=", self.zone_id.id)])
        if len(locations) == 1:
            self.location_id = locations
            self._load_scope_lines()

    @api.onchange("zone_id")
    def _onchange_zone_cascade(self):
        self.location_id = False
        self.line_ids = [(5, 0, 0)]
        if self.zone_id:
            self.project_id = self.zone_id.project_id
            self.customer_id = self.zone_id.customer_id
            self._select_zone_children()

    @api.onchange("location_id")
    def _onchange_location(self):
        if self.location_id:
            self.zone_id = self.location_id.zone_id
            self.project_id = self.location_id.project_id
            self.customer_id = self.location_id.customer_id
            self._load_scope_lines()

    def _load_scope_lines(self):
        if not self.location_id:
            return
        commands = [(5, 0, 0)]
        grouped_scope = {}
        for requirement in self.location_id.requirement_ids.filtered("active"):
            values = grouped_scope.setdefault(requirement.resource_type_id, {
                "scope": 0,
                "resources": self.env["gsus.resource"],
                "vehicles": self.env["fleet.vehicle"],
            })
            values["scope"] += requirement.required_count
            values["resources"] |= requirement.assigned_resource_ids
            values["vehicles"] |= requirement.assigned_vehicle_ids
        for resource_type, values in grouped_scope.items():
            required_count = values["scope"]
            # The FRT scope is the permanent daily baseline.  Copy every active
            # employee/vehicle assigned on the master irrespective of its global
            # workflow status; leave/replacement changes are recorded only on
            # the daily availability entry.
            resources = values["resources"].filtered("active")
            vehicles = values["vehicles"].filtered("active")
            commands.append((0, 0, {
                "resource_type_id": resource_type.id,
                "scope_count": required_count,
                "available_resource_ids": [(6, 0, resources.ids)] if resource_type.category != "vehicle" else False,
                "available_vehicle_ids": [(6, 0, vehicles.ids)] if resource_type.category == "vehicle" else False,
                "available_count": len(vehicles) if resource_type.category == "vehicle" else len(resources),
            }))
        self.line_ids = commands
        self._sync_vehicle_km_capture()

    def _sync_vehicle_km_capture(self):
        """Keep one KM entry for every regular or alternate vehicle on the day."""
        VehicleKm = self.env["gsus.frt.vehicle.km"]
        for sheet in self:
            if not sheet.id:
                continue
            regular_vehicles = sheet.line_ids.mapped("available_vehicle_ids")
            alternate_vehicles = sheet.line_ids.mapped("alternate_vehicle_ids")
            selected_vehicles = regular_vehicles | alternate_vehicles
            existing = sheet.vehicle_km_line_ids
            obsolete = existing.filtered(lambda item: item.vehicle_id not in selected_vehicles)
            # An unfilled line has no operational data, so it can safely vanish
            # if the vehicle was removed from the availability sheet.
            obsolete.filtered(lambda item: not item.ending_km and not item.total_km).unlink()
            for vehicle in selected_vehicles - existing.mapped("vehicle_id"):
                VehicleKm.create({
                    "availability_id": sheet.id,
                    "vehicle_id": vehicle.id,
                    "is_alternate": vehicle in alternate_vehicles,
                })
            for entry in existing.filtered(lambda item: item.vehicle_id in selected_vehicles):
                entry.is_alternate = entry.vehicle_id in alternate_vehicles

    def _sync_alternate_allocations(self):
        """Create dashboard rows for people replaced on this daily FRT sheet.

        A normal workflow is: remove the unavailable regular person and select
        the replacement in Alternate Persons.  Pair them by resource type in a
        stable order, so every temporary replacement is reportable.
        """
        Allocation = self.env["gsus.frt.alternate.allocation"].sudo()
        for sheet in self:
            Allocation.search([("availability_id", "=", sheet.id)]).unlink()
            for line in sheet.line_ids.filtered(lambda item: item.resource_category != "vehicle"):
                originals = line.unavailable_resource_ids.sorted("id")
                alternates = line.alternate_resource_ids.sorted("id")
                for index, alternate in enumerate(alternates):
                    Allocation.create({
                        "availability_id": sheet.id,
                        "line_id": line.id,
                        "resource_type_id": line.resource_type_id.id,
                        "original_resource_id": originals[index].id if index < len(originals) else False,
                        "alternate_resource_id": alternate.id,
                    })

    def write(self, vals):
        if any(key in vals for key in ("line_ids", "notes", "responsible_id")):
            vals.update({"as_on": fields.Datetime.now(), "updated_by": self.env.user.id})
        return super().write(vals)

    def action_refresh(self):
        for record in self:
            record.write({"as_on": fields.Datetime.now(), "updated_by": self.env.user.id})
            record.line_ids._create_history("Availability refreshed")
        return True

    def action_load_scope(self):
        for record in self:
            record._load_scope_lines()
            record.write({"as_on": fields.Datetime.now(), "updated_by": self.env.user.id})
        return True

    def action_confirm_attendance(self):
        if not self:
            raise UserError(_("Select at least one FRT Availability record."))
        dates = set(self.mapped("availability_date"))
        if len(dates) != 1:
            raise UserError(_("Select records from only one availability date."))
        attendance_date = next(iter(dates))
        employee_allocations = {}
        for sheet in self:
            for line in sheet.line_ids.filtered(lambda item: item.resource_category != "vehicle"):
                regular_ids = set(line.available_resource_ids.ids)
                for resource in line.available_resource_ids | line.alternate_resource_ids:
                    if not resource.employee_id:
                        employee = self.env["hr.employee"].create({"name": resource.name})
                        resource.employee_id = employee
                    if resource.employee_id.id in employee_allocations:
                        other = employee_allocations[resource.employee_id.id][0]
                        raise UserError(_("%s is selected in both %s and %s.", resource.name, other.location_id.name, sheet.location_id.name))
                    employee_allocations[resource.employee_id.id] = (sheet, resource, resource.id not in regular_ids)
        if not employee_allocations:
            raise UserError(_("No available or alternate persons were found in the selected records."))

        Attendance = self.env["hr.attendance"].sudo()
        timezone = pytz.timezone(self.env.user.tz or "Asia/Kolkata")
        created = Attendance.browse()
        skipped = 0
        for employee_id, (sheet, resource, is_alternate) in employee_allocations.items():
            shift = resource.shift_id
            def as_time(decimal_time):
                hours = int(decimal_time)
                minutes = round((decimal_time - hours) * 60)
                if minutes == 60:
                    hours += 1
                    minutes = 0
                return time(hours % 24, minutes)
            local_check_in = datetime.combine(attendance_date, as_time(shift.start_time))
            # End time earlier than (or equal to) start time means the duty ends next day.
            end_date = attendance_date + timedelta(days=1) if shift.end_time <= shift.start_time else attendance_date
            local_check_out = datetime.combine(end_date, as_time(shift.end_time))
            check_in = timezone.localize(local_check_in).astimezone(pytz.UTC).replace(tzinfo=None)
            check_out = timezone.localize(local_check_out).astimezone(pytz.UTC).replace(tzinfo=None)
            duplicate = Attendance.search_count([
                ("employee_id", "=", employee_id), ("check_in", "<", check_out),
                "|", ("check_out", "=", False), ("check_out", ">", check_in),
            ])
            if duplicate:
                skipped += 1
                continue
            created |= Attendance.create({
                "employee_id": employee_id, "check_in": check_in, "check_out": check_out,
                "gsus_availability_id": sheet.id, "gsus_location_id": sheet.location_id.id,
                "gsus_shift_id": resource.shift_id.id, "gsus_is_alternate": is_alternate,
            })
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Attendance Created"),
            "message": _("Created %(created)s attendance records; skipped %(skipped)s duplicates.", created=len(created), skipped=skipped),
            "type": "success", "sticky": False,
        }}


class GsusDailyEntryWizard(models.TransientModel):
    _name = "gsus.daily.entry.wizard"
    _description = "Create Daily FRT Availability Entries"

    customer_id = fields.Many2one(
        "res.partner", required=True, domain=[("is_telecom_customer", "=", True)]
    )
    project_id = fields.Many2one(
        "project.project", required=True,
        domain="[('customer_id', '=', customer_id), ('telecom_active', '=', True)]",
    )
    availability_date = fields.Date(
        string="Date", required=True,
        default=lambda self: fields.Date.context_today(self) + timedelta(days=1),
    )

    @api.onchange("customer_id")
    def _onchange_customer_id(self):
        self.project_id = False
        projects = self.env["project.project"].search([
            ("customer_id", "=", self.customer_id.id), ("telecom_active", "=", True)
        ])
        if len(projects) == 1:
            self.project_id = projects

    def action_create_entries(self):
        self.ensure_one()
        if self.project_id.customer_id != self.customer_id:
            raise ValidationError(_("The selected project does not belong to the selected customer."))
        locations = self.env["gsus.location"].search([
            ("project_id", "=", self.project_id.id), ("active", "=", True)
        ], order="zone_id, name")
        if not locations:
            raise ValidationError(_("No active FRT Locations were found for this project."))

        Availability = self.env["gsus.frt.availability"]
        existing = Availability.search([
            ("location_id", "in", locations.ids),
            ("availability_date", "=", self.availability_date),
        ])
        existing_location_ids = set(existing.mapped("location_id").ids)
        created = Availability.browse()
        for location in locations.filtered(lambda item: item.id not in existing_location_ids):
            record = Availability.create({
                "customer_id": self.customer_id.id,
                "project_id": self.project_id.id,
                "zone_id": location.zone_id.id,
                "location_id": location.id,
                "availability_date": self.availability_date,
                "responsible_id": location.responsible_id.id,
            })
            record._load_scope_lines()
            created |= record

        return {
            "type": "ir.actions.act_window",
            "name": _("FRT Availability - %s", self.availability_date),
            "res_model": "gsus.frt.availability",
            "view_mode": "list,form,pivot",
            "domain": [("project_id", "=", self.project_id.id),
                       ("availability_date", "=", self.availability_date)],
            "context": {"group_by": ["zone_id"]},
            "target": "current",
        }


class GsusAttendanceConfirmWizard(models.TransientModel):
    _name = "gsus.attendance.confirm.wizard"
    _description = "Confirm GSUS Attendance by Date"

    attendance_date = fields.Date(string="Attendance Date", required=True, default=fields.Date.context_today)

    def action_create_attendance(self):
        self.ensure_one()
        sheets = self.env["gsus.frt.availability"].search([
            ("availability_date", "=", self.attendance_date),
        ])
        if not sheets:
            raise UserError(_("No FRT Availability records were found for %s.", self.attendance_date))
        return sheets.action_confirm_attendance()


class GsusFrtAvailabilityLine(models.Model):
    _name = "gsus.frt.availability.line"
    _description = "FRT Scope and Current Availability"
    _order = "resource_type_id"

    availability_id = fields.Many2one("gsus.frt.availability", required=True, ondelete="cascade")
    location_id = fields.Many2one(related="availability_id.location_id", store=True)
    zone_id = fields.Many2one(related="availability_id.zone_id", store=True)
    project_id = fields.Many2one(related="availability_id.project_id", store=True)
    customer_id = fields.Many2one(related="availability_id.customer_id", store=True)
    resource_type_id = fields.Many2one("gsus.resource.type", required=True, ondelete="restrict")
    scope_count = fields.Integer(string="Scope", required=True, default=1)
    available_resource_ids = fields.Many2many(
        "gsus.resource", "gsus_frt_line_resource_rel", "line_id", "resource_id",
        string="Available Persons / Vehicles",
        domain="[('resource_type_id', '=', resource_type_id), ('status', '=', 'available'), ('active', '=', True)]",
    )
    unavailable_resource_ids = fields.Many2many(
        "gsus.resource", "gsus_frt_line_unavailable_resource_rel", "line_id", "resource_id",
        string="Not Available Persons",
        domain="[('resource_type_id', '=', resource_type_id), ('active', '=', True)]",
        help="Persons removed from today's regular availability, for example due to leave.",
    )
    resource_category = fields.Selection(related="resource_type_id.category")
    available_vehicle_ids = fields.Many2many(
        "fleet.vehicle", "gsus_frt_line_vehicle_rel", "line_id", "vehicle_id",
        string="Available Vehicles",
        domain="[('active', '=', True), ('gsus_status', '=', 'available')]",
    )
    alternate_resource_ids = fields.Many2many(
        "gsus.resource", "gsus_frt_line_alternate_resource_rel", "line_id", "resource_id",
        string="Alternate Persons",
        domain="[('resource_type_id', '=', resource_type_id), ('status', '=', 'available'), ('active', '=', True), ('id', 'not in', available_resource_ids), ('id', 'not in', unavailable_resource_ids)]",
    )
    alternate_vehicle_ids = fields.Many2many(
        "fleet.vehicle", "gsus_frt_line_alternate_vehicle_rel", "line_id", "vehicle_id",
        string="Alternate Vehicles",
        domain="[('active', '=', True), ('gsus_status', '=', 'available'), ('id', 'not in', available_vehicle_ids)]",
    )
    available_count = fields.Integer(string="Available", default=0)
    alternate_count = fields.Integer(string="Temporary / Alternate", default=0)
    total_available = fields.Integer(compute="_compute_counts", store=True)
    gap_count = fields.Integer(compute="_compute_counts", store=True)
    gap_start_date = fields.Date(readonly=True)
    aging_days = fields.Integer(compute="_compute_aging")
    expected_closure_date = fields.Date()
    responsible_id = fields.Many2one("res.users")
    gap_status = fields.Selection([
        ("no_gap", "No Gap"), ("open", "Open"),
        ("temporary", "Temporary Arrangement"), ("resolved", "Resolved"),
    ], compute="_compute_counts", store=True)
    remarks = fields.Char()
    _type_unique = models.Constraint("UNIQUE(availability_id, resource_type_id)", "A resource type can appear only once in an FRT scope.")

    @api.depends("scope_count", "available_count", "alternate_count")
    def _compute_counts(self):
        today = fields.Date.context_today(self)
        for line in self:
            old_gap = line.gap_count if line.id else 0
            line.total_available = line.available_count + line.alternate_count
            line.gap_count = max(line.scope_count - line.total_available, 0)
            if line.gap_count:
                line.gap_status = "temporary" if line.alternate_count else "open"
                if not line.gap_start_date:
                    line.gap_start_date = today
            else:
                line.gap_status = "resolved" if old_gap else "no_gap"
                line.gap_start_date = False

    @api.onchange(
        "available_resource_ids", "available_vehicle_ids",
        "alternate_resource_ids", "alternate_vehicle_ids", "resource_type_id",
    )
    def _onchange_available_resources(self):
        for line in self:
            if line.resource_category != "vehicle" and line._origin:
                removed = line._origin.available_resource_ids - line.available_resource_ids
                line.unavailable_resource_ids = (
                    (line.unavailable_resource_ids | removed)
                    - line.available_resource_ids
                    - line.alternate_resource_ids
                )
            line.available_count = len(line.available_vehicle_ids) if line.resource_category == "vehicle" else len(line.available_resource_ids)
            line.alternate_count = len(line.alternate_vehicle_ids) if line.resource_category == "vehicle" else len(line.alternate_resource_ids)

    @api.constrains(
        "available_resource_ids", "available_vehicle_ids",
        "alternate_resource_ids", "alternate_vehicle_ids",
    )
    def _check_duplicate_allocations(self):
        for line in self:
            if (line.available_resource_ids & line.alternate_resource_ids) or (line.available_vehicle_ids & line.alternate_vehicle_ids):
                raise ValidationError(_("The same person or vehicle cannot be both regular and alternate."))

    @api.constrains("available_resource_ids", "availability_id", "resource_type_id")
    def _check_regular_person_location_allocation(self):
        """A regular person can work at one FRT only for one availability date.

        Alternates are intentionally excluded: they are a replacement pool and
        can be selected wherever an operational replacement is required.
        """
        for line in self.filtered(lambda item: item.resource_category != "vehicle" and item.available_resource_ids):
            date = line.availability_id.availability_date
            if not date:
                continue
            other_lines = self.search([
                ("id", "not in", line.ids),
                ("availability_id.availability_date", "=", date),
                ("availability_id.location_id", "!=", line.location_id.id),
                ("available_resource_ids", "in", line.available_resource_ids.ids),
            ])
            duplicate_people = line.available_resource_ids & other_lines.mapped("available_resource_ids")
            if duplicate_people:
                locations = ", ".join(sorted(set(other_lines.filtered(
                    lambda item: item.available_resource_ids & duplicate_people
                ).mapped("location_id.name"))))
                raise ValidationError(_(
                    "%(people)s is already allocated as a regular person at %(locations)s on %(date)s. "
                    "Use Alternate Persons if this is a replacement allocation."
                ) % {
                    "people": ", ".join(duplicate_people.mapped("name")),
                    "locations": locations,
                    "date": fields.Date.to_string(date),
                })

        for line in self.filtered(lambda item: item.resource_category == "vehicle" and item.available_vehicle_ids):
            date = line.availability_id.availability_date
            if not date:
                continue
            other_lines = self.search([
                ("id", "not in", line.ids),
                ("availability_id.availability_date", "=", date),
                ("availability_id.location_id", "!=", line.location_id.id),
                ("available_vehicle_ids", "in", line.available_vehicle_ids.ids),
            ])
            duplicate_vehicles = line.available_vehicle_ids & other_lines.mapped("available_vehicle_ids")
            if duplicate_vehicles:
                locations = ", ".join(sorted(set(other_lines.filtered(
                    lambda item: item.available_vehicle_ids & duplicate_vehicles
                ).mapped("location_id.name"))))
                raise ValidationError(_(
                    "%(vehicles)s is already allocated as a regular vehicle at %(locations)s on %(date)s. "
                    "Use Alternate Vehicles if this is a replacement allocation."
                ) % {
                    "vehicles": ", ".join(duplicate_vehicles.mapped("license_plate")),
                    "locations": locations,
                    "date": fields.Date.to_string(date),
                })

    @api.depends("gap_start_date", "gap_count")
    def _compute_aging(self):
        today = fields.Date.context_today(self)
        for line in self:
            line.aging_days = (today - line.gap_start_date).days if line.gap_count and line.gap_start_date else 0

    @api.constrains("scope_count", "alternate_count")
    def _check_counts(self):
        if any(line.scope_count < 0 or line.alternate_count < 0 for line in self):
            raise ValidationError("Scope and temporary/alternate counts cannot be negative.")

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._create_history("Scope line created")
        records.mapped("availability_id")._sync_vehicle_km_capture()
        records.mapped("availability_id")._sync_alternate_allocations()
        return records

    def write(self, vals):
        tracked = {
            "scope_count", "available_count", "available_resource_ids", "available_vehicle_ids",
            "alternate_count", "alternate_resource_ids", "alternate_vehicle_ids",
            "unavailable_resource_ids", "remarks",
        }
        before = {line.id: (line.scope_count, line.available_count, line.gap_count) for line in self}
        sync_unavailable = (
            {"available_resource_ids", "alternate_resource_ids"}.intersection(vals)
            and not self.env.context.get("skip_unavailable_sync")
        )
        old_available = {
            line.id: line.available_resource_ids
            for line in self
        } if sync_unavailable else {}
        result = super().write(vals)
        if {"available_vehicle_ids", "alternate_vehicle_ids"}.intersection(vals):
            self.mapped("availability_id")._sync_vehicle_km_capture()
        if old_available:
            for line in self:
                removed = old_available[line.id] - line.available_resource_ids
                unavailable = (
                    (line.unavailable_resource_ids | removed)
                    - line.available_resource_ids
                    - line.alternate_resource_ids
                )
                if unavailable != line.unavailable_resource_ids:
                    line.with_context(skip_unavailable_sync=True).write({
                        "unavailable_resource_ids": [(6, 0, unavailable.ids)],
                    })
        if tracked.intersection(vals):
            for line in self:
                old_scope, old_available, old_gap = before[line.id]
                line._create_history("Availability updated", old_scope, old_available, old_gap)
            self.mapped("availability_id").write({"as_on": fields.Datetime.now(), "updated_by": self.env.user.id})
        if {"available_resource_ids", "alternate_resource_ids", "unavailable_resource_ids"}.intersection(vals):
            self.mapped("availability_id")._sync_alternate_allocations()
        return result

    def _create_history(self, reason, old_scope=None, old_available=None, old_gap=None):
        History = self.env["gsus.frt.availability.history"]
        for line in self:
            History.create({
                "availability_id": line.availability_id.id,
                "line_id": line.id,
                "resource_type_id": line.resource_type_id.id,
                "old_scope": line.scope_count if old_scope is None else old_scope,
                "new_scope": line.scope_count,
                "old_available": line.available_count if old_available is None else old_available,
                "new_available": line.available_count,
                "old_gap": line.gap_count if old_gap is None else old_gap,
                "new_gap": line.gap_count,
                "reason": line.remarks or reason,
            })


class GsusFrtVehicleKm(models.Model):
    _name = "gsus.frt.vehicle.km"
    _description = "FRT Vehicle KM Capture"
    _rec_name = "name"
    _order = "availability_date desc, location_id, vehicle_id"

    availability_id = fields.Many2one("gsus.frt.availability", required=True, ondelete="cascade", index=True)
    availability_date = fields.Date(related="availability_id.availability_date", store=True, index=True)
    location_id = fields.Many2one(related="availability_id.location_id", store=True, index=True)
    customer_id = fields.Many2one(related="availability_id.customer_id", store=True, index=True)
    project_id = fields.Many2one(related="availability_id.project_id", store=True)
    zone_id = fields.Many2one(related="availability_id.zone_id", store=True)
    vehicle_id = fields.Many2one("fleet.vehicle", required=True, ondelete="restrict", index=True)
    name = fields.Char(related="vehicle_id.display_name", string="Vehicle", store=True, readonly=True)
    vehicle_number = fields.Char(related="vehicle_id.license_plate", string="Vehicle Number", store=True)
    is_alternate = fields.Boolean(string="Alternate Vehicle")
    replaces_km_id = fields.Many2one(
        "gsus.frt.vehicle.km", string="Replacing Vehicle",
        domain="[('availability_id', '=', availability_id), ('is_alternate', '=', False)]",
        ondelete="set null",
        help="Select the breakdown regular vehicle that this alternate vehicle replaced.",
    )
    replacement_km_ids = fields.One2many("gsus.frt.vehicle.km", "replaces_km_id", string="Replacement KM Entries")
    starting_km = fields.Float(string="Starting KM", digits=(16, 2))
    ending_km = fields.Float(string="Ending KM", digits=(16, 2))
    total_km = fields.Float(string="Total KM", compute="_compute_total_km", store=True, digits=(16, 2))
    replacement_km = fields.Float(string="Replacement KM", compute="_compute_operational_km", store=True, digits=(16, 2))
    operational_km = fields.Float(string="Operational Cumulative KM", compute="_compute_operational_km", store=True, digits=(16, 2))
    _vehicle_day_unique = models.Constraint(
        "UNIQUE(availability_id, vehicle_id)",
        "A vehicle can have only one KM capture entry per FRT availability record.",
    )

    def init(self):
        """Backfill the KM tab for availability sheets created before this feature."""
        self.env.cr.execute("""
            INSERT INTO gsus_frt_vehicle_km (availability_id, vehicle_id, is_alternate,
                                             create_uid, create_date, write_uid, write_date)
            SELECT line.availability_id, relation.vehicle_id, relation.is_alternate,
                   1, NOW(), 1, NOW()
              FROM (
                    SELECT line_id, vehicle_id, FALSE AS is_alternate
                      FROM gsus_frt_line_vehicle_rel
                    UNION
                    SELECT line_id, vehicle_id, TRUE AS is_alternate
                      FROM gsus_frt_line_alternate_vehicle_rel
                   ) AS relation
              JOIN gsus_frt_availability_line AS line ON line.id = relation.line_id
            ON CONFLICT (availability_id, vehicle_id)
            DO UPDATE SET is_alternate = EXCLUDED.is_alternate,
                          write_uid = EXCLUDED.write_uid,
                          write_date = EXCLUDED.write_date
        """)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "starting_km" in vals or not vals.get("vehicle_id") or not vals.get("availability_id"):
                continue
            availability = self.env["gsus.frt.availability"].browse(vals["availability_id"])
            previous = self.search([
                ("vehicle_id", "=", vals["vehicle_id"]),
                ("availability_date", "<", availability.availability_date),
                ("ending_km", ">", 0),
            ], order="availability_date desc, id desc", limit=1)
            if previous:
                vals["starting_km"] = previous.ending_km
        return super().create(vals_list)

    @api.depends("starting_km", "ending_km")
    def _compute_total_km(self):
        for line in self:
            line.total_km = line.ending_km - line.starting_km if line.ending_km else 0

    @api.depends("total_km", "replacement_km_ids.total_km")
    def _compute_operational_km(self):
        for line in self:
            line.replacement_km = sum(line.replacement_km_ids.mapped("total_km"))
            line.operational_km = line.total_km + line.replacement_km

    @api.constrains("replaces_km_id", "availability_id", "is_alternate")
    def _check_replacement_vehicle(self):
        for line in self:
            if not line.replaces_km_id:
                continue
            if not line.is_alternate:
                raise ValidationError(_("Only an Alternate Vehicle can replace a regular vehicle."))
            if line.replaces_km_id.availability_id != line.availability_id:
                raise ValidationError(_("The replaced vehicle must belong to the same FRT and date."))
            if line.replaces_km_id.is_alternate:
                raise ValidationError(_("An alternate vehicle must be linked to a regular vehicle, not another alternate."))

    @api.constrains("starting_km", "ending_km")
    def _check_km_values(self):
        for line in self:
            if line.starting_km < 0 or line.ending_km < 0:
                raise ValidationError(_("Starting KM and Ending KM cannot be negative."))
            if line.ending_km and line.ending_km < line.starting_km:
                raise ValidationError(_("Ending KM cannot be less than Starting KM."))


class GsusFrtAlternateAllocation(models.Model):
    _name = "gsus.frt.alternate.allocation"
    _description = "Temporary Alternate Employee Allocation"
    _order = "availability_date desc, location_id, resource_type_id, id"

    availability_id = fields.Many2one("gsus.frt.availability", required=True, ondelete="cascade", index=True)
    line_id = fields.Many2one("gsus.frt.availability.line", required=True, ondelete="cascade")
    availability_date = fields.Date(related="availability_id.availability_date", store=True, index=True)
    customer_id = fields.Many2one(related="availability_id.customer_id", store=True, index=True)
    project_id = fields.Many2one(related="availability_id.project_id", store=True)
    zone_id = fields.Many2one(related="availability_id.zone_id", store=True)
    location_id = fields.Many2one(related="availability_id.location_id", store=True, index=True)
    resource_type_id = fields.Many2one("gsus.resource.type", required=True, ondelete="restrict")
    original_resource_id = fields.Many2one("gsus.resource", string="Original / Unavailable Person", ondelete="set null")
    alternate_resource_id = fields.Many2one("gsus.resource", string="Temporary Alternate Person", required=True, ondelete="cascade")
    original_employee_id = fields.Many2one(related="original_resource_id.employee_id", string="Original Employee", store=True)
    alternate_employee_id = fields.Many2one(related="alternate_resource_id.employee_id", string="Alternate Employee", store=True)
    remarks = fields.Char(related="line_id.remarks", readonly=True)


class GsusFrtAvailabilityHistory(models.Model):
    _name = "gsus.frt.availability.history"
    _description = "FRT Availability Change History"
    _order = "changed_on desc, id desc"

    availability_id = fields.Many2one("gsus.frt.availability", required=True, ondelete="cascade")
    line_id = fields.Many2one("gsus.frt.availability.line", ondelete="set null")
    location_id = fields.Many2one(related="availability_id.location_id", store=True)
    resource_type_id = fields.Many2one("gsus.resource.type", required=True)
    old_scope = fields.Integer()
    new_scope = fields.Integer()
    old_available = fields.Integer()
    new_available = fields.Integer()
    old_gap = fields.Integer()
    new_gap = fields.Integer()
    reason = fields.Char()
    changed_on = fields.Datetime(default=fields.Datetime.now, readonly=True)
    changed_by = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)


class HrAttendance(models.Model):
    _inherit = "hr.attendance"

    gsus_availability_id = fields.Many2one("gsus.frt.availability", ondelete="set null", index=True)
    gsus_location_id = fields.Many2one("gsus.location", string="FRT", ondelete="set null", index=True)
    gsus_shift_id = fields.Many2one("gsus.shift", string="GSUS Shift", ondelete="set null")
    gsus_is_alternate = fields.Boolean(string="Alternate Duty")
