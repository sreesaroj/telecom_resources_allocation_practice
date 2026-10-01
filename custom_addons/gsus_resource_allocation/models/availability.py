from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class GsusAvailability(models.Model):
    _name = "gsus.availability"
    _description = "GSUS Daily Availability"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "availability_date desc, id desc"

    name = fields.Char(default="New", readonly=True, copy=False)
    availability_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    shift = fields.Selection([("day", "Day"), ("night", "Night")], required=True, default="day", tracking=True)
    location_id = fields.Many2one("gsus.location", required=True, ondelete="restrict", tracking=True)
    service_category_id = fields.Many2one("gsus.service.category", string="Service Category", tracking=True)
    zone_id = fields.Many2one(related="location_id.zone_id", store=True)
    line_ids = fields.One2many("gsus.availability.line", "availability_id", copy=True)
    state = fields.Selection([("draft", "Draft"), ("submitted", "Submitted"), ("approved", "Approved")], default="draft", tracking=True)
    total_required = fields.Integer(compute="_compute_totals", store=True)
    total_available = fields.Integer(compute="_compute_totals", store=True)
    total_gap = fields.Integer(compute="_compute_totals", store=True)
    availability_percent = fields.Float(compute="_compute_totals", store=True)
    notes = fields.Text()
    _snapshot_unique = models.Constraint(
        "UNIQUE(availability_date, shift, location_id, service_category_id)", "An availability sheet already exists for this FRT, service, date and shift."
    )

    @api.depends("line_ids.required_count", "line_ids.available_count", "line_ids.gap_count")
    def _compute_totals(self):
        for record in self:
            record.total_required = sum(record.line_ids.mapped("required_count"))
            record.total_available = sum(record.line_ids.mapped("available_count"))
            record.total_gap = sum(record.line_ids.mapped("gap_count"))
            record.availability_percent = record.total_required and min(record.total_available / record.total_required * 100, 100) or 100

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("gsus.availability") or "New"
        return super().create(vals_list)

    @api.onchange("location_id", "service_category_id", "shift")
    def _onchange_location_id(self):
        if self.location_id:
            self.line_ids = self._prepare_scope_lines()

    def _matching_shift_ids(self):
        """Translate the simple daily shift into detailed resource shifts."""
        codes = ["FULL24", "DAY12" if self.shift == "day" else "NIGHT12"]
        return self.env["gsus.shift"].search([("code", "in", codes)]).ids

    def _prepare_scope_lines(self):
        self.ensure_one()
        shift_ids = self._matching_shift_ids()
        requirements = self.location_id.requirement_ids.filtered(
            lambda req: req.active
            and (not self.service_category_id or not req.service_category_id or req.service_category_id == self.service_category_id)
            and (not req.shift_id or req.shift_id.id in shift_ids)
        )
        grouped = {}
        for requirement in requirements:
            grouped.setdefault(requirement.resource_type_id, 0)
            grouped[requirement.resource_type_id] += requirement.required_count
        commands = []
        for resource_type, required_count in grouped.items():
            resources = self.env["gsus.resource"].search([
                ("location_id", "=", self.location_id.id),
                ("resource_type_id", "=", resource_type.id),
                ("shift_id", "in", shift_ids),
                ("status", "in", ["available", "reserved", "allocated", "deployed"]),
            ] + ([("service_category_id", "=", self.service_category_id.id)] if self.service_category_id else []))
            commands.append((0, 0, {
                "resource_type_id": resource_type.id,
                "required_count": required_count,
                "available_count": len(resources),
                "actual_resource_ids": [(6, 0, resources.ids)],
            }))
        return commands

    def action_load_scope(self):
        self.ensure_one()
        if not self.location_id:
            raise UserError("Select an FRT location first.")
        self.line_ids = [(5, 0, 0)] + self._prepare_scope_lines()

    def action_refresh_resources(self):
        """Rebuild the snapshot from current actual resources and requirements."""
        return self.action_load_scope()

    def action_submit(self):
        for record in self:
            if not record.line_ids:
                raise UserError("Add availability lines before submitting.")
            record.state = "submitted"
            record._sync_gaps()

    def action_approve(self):
        self.write({"state": "approved"})
        self._sync_gaps()

    def action_reset_draft(self):
        self.write({"state": "draft"})

    def _sync_gaps(self):
        Gap = self.env["gsus.gap"]
        for record in self:
            for line in record.line_ids:
                existing = Gap.search([("availability_line_id", "=", line.id)], limit=1)
                if line.gap_count:
                    values = {
                        "availability_line_id": line.id,
                        "location_id": record.location_id.id,
                        "resource_type_id": line.resource_type_id.id,
                        "gap_count": line.gap_count,
                        "reason": line.remarks,
                    }
                    existing.write(values) if existing else Gap.create(values)
                elif existing and existing.state != "resolved":
                    existing.action_resolve()


class GsusAvailabilityLine(models.Model):
    _name = "gsus.availability.line"
    _description = "GSUS Availability Line"
    _order = "resource_type_id"

    availability_id = fields.Many2one("gsus.availability", required=True, ondelete="cascade")
    resource_type_id = fields.Many2one("gsus.resource.type", required=True, ondelete="restrict")
    required_count = fields.Integer(required=True)
    available_count = fields.Integer(required=True, default=0)
    temporary_count = fields.Integer(default=0)
    alternate_count = fields.Integer(default=0)
    gap_count = fields.Integer(compute="_compute_counts", store=True)
    excess_count = fields.Integer(compute="_compute_counts", store=True)
    worker_ids = fields.Many2many("gsus.worker", string="Available Workers")
    actual_resource_ids = fields.Many2many(
        "gsus.resource", "gsus_availability_resource_rel", "line_id", "resource_id",
        string="Actual Available Resources",
    )
    absent_worker_ids = fields.Many2many("gsus.worker", "gsus_availability_absent_rel", string="Absent Workers")
    remarks = fields.Char()

    @api.depends("required_count", "available_count")
    def _compute_counts(self):
        for line in self:
            line.gap_count = max(line.required_count - line.available_count, 0)
            line.excess_count = max(line.available_count - line.required_count, 0)

    @api.constrains("required_count", "available_count", "temporary_count", "alternate_count")
    def _check_non_negative(self):
        for line in self:
            if min(line.required_count, line.available_count, line.temporary_count, line.alternate_count) < 0:
                raise ValidationError("Resource counts cannot be negative.")


class GsusGap(models.Model):
    _name = "gsus.gap"
    _description = "GSUS Resource Gap"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "state, gap_start_date, id desc"

    name = fields.Char(default="New", readonly=True, copy=False)
    availability_line_id = fields.Many2one("gsus.availability.line", ondelete="set null")
    location_id = fields.Many2one("gsus.location", required=True, ondelete="restrict", tracking=True)
    zone_id = fields.Many2one(related="location_id.zone_id", store=True)
    project_id = fields.Many2one(related="location_id.project_id", store=True)
    customer_id = fields.Many2one(related="location_id.customer_id", store=True)
    shift_id = fields.Many2one("gsus.shift", tracking=True)
    resource_type_id = fields.Many2one("gsus.resource.type", required=True, ondelete="restrict", tracking=True)
    worker_id = fields.Many2one("gsus.worker", string="Affected / Missing Worker", tracking=True)
    replacement_worker_id = fields.Many2one("gsus.worker", tracking=True)
    gap_count = fields.Integer(required=True, default=1, tracking=True)
    gap_start_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    aging_days = fields.Integer(compute="_compute_aging", search="_search_aging_days")
    expected_closure_date = fields.Date(tracking=True)
    responsible_id = fields.Many2one("res.users", tracking=True)
    reason = fields.Text()
    action_taken = fields.Text()
    state = fields.Selection([
        ("open", "Open"), ("planned", "Replacement Planned"),
        ("temporary", "Temporarily Filled"), ("resolved", "Resolved"),
    ], default="open", tracking=True)
    resolved_date = fields.Date(readonly=True)

    @api.depends("gap_start_date", "state", "resolved_date")
    def _compute_aging(self):
        today = fields.Date.context_today(self)
        for record in self:
            end = record.resolved_date if record.state == "resolved" and record.resolved_date else today
            record.aging_days = max((end - record.gap_start_date).days, 0) if record.gap_start_date else 0

    @api.model
    def _search_aging_days(self, operator, value):
        if operator not in (">=", ">", "<=", "<", "=", "!="):
            raise UserError("Unsupported aging search operator.")
        cutoff = fields.Date.subtract(fields.Date.context_today(self), days=int(value))
        reverse_operator = {">=": "<=", ">": "<", "<=": ">=", "<": ">", "=": "=", "!=": "!="}[operator]
        return [("gap_start_date", reverse_operator, cutoff)]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("gsus.gap") or "New"
        return super().create(vals_list)

    def action_plan(self):
        self.write({"state": "planned"})

    def action_temporary(self):
        self.write({"state": "temporary"})

    def action_resolve(self):
        self.write({"state": "resolved", "resolved_date": fields.Date.context_today(self)})
