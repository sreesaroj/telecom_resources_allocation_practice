from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class GsusResourceRequest(models.Model):
    _name = "gsus.resource.request"
    _description = "GSUS Vendor Resource Request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "request_date desc, id desc"

    name = fields.Char(default="New", readonly=True, copy=False)
    vendor_id = fields.Many2one("res.partner", required=True, default=lambda self: self.env.user.partner_id, tracking=True)
    customer_id = fields.Many2one("res.partner", required=True, domain=[("is_telecom_customer", "=", True)], tracking=True)
    project_id = fields.Many2one("project.project", required=True, domain="[('customer_id', '=', customer_id)]", tracking=True)
    service_category_id = fields.Many2one("gsus.service.category", string="Service Category", tracking=True)
    zone_id = fields.Many2one("gsus.zone", required=True, domain="[('project_id', '=', project_id)]", tracking=True)
    location_id = fields.Many2one("gsus.location", required=True, domain="[('zone_id', '=', zone_id)]", tracking=True)
    shift_id = fields.Many2one("gsus.shift", required=True, tracking=True)
    required_from = fields.Datetime(required=True, tracking=True)
    required_to = fields.Datetime(required=True, tracking=True)
    request_date = fields.Datetime(default=fields.Datetime.now, readonly=True)
    state = fields.Selection([
        ("draft", "Draft"), ("submitted", "Submitted"), ("review", "Under Review"),
        ("approved", "Approved"), ("allocated", "Allocated"), ("deployed", "Deployed"), ("completed", "Completed"),
        ("rejected", "Rejected"), ("cancelled", "Cancelled"),
    ], default="draft", tracking=True)
    request_line_ids = fields.One2many("gsus.resource.request.line", "request_id", copy=True)
    resource_count = fields.Integer(compute="_compute_resource_count", store=True)
    approved_by = fields.Many2one("res.users", readonly=True, tracking=True)
    approved_date = fields.Datetime(readonly=True)
    notes = fields.Text()

    @api.depends("request_line_ids")
    def _compute_resource_count(self):
        for record in self: record.resource_count = len(record.request_line_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New": vals["name"] = self.env["ir.sequence"].next_by_code("gsus.resource.request") or "New"
        return super().create(vals_list)

    @api.constrains("required_from", "required_to")
    def _check_dates(self):
        if any(record.required_to <= record.required_from for record in self):
            raise ValidationError("Required To must be later than Required From.")

    @api.constrains("request_line_ids", "location_id", "shift_id")
    def _check_line_hierarchy(self):
        for record in self:
            if any(line.resource_id.location_id != record.location_id or line.resource_id.shift_id != record.shift_id for line in record.request_line_ids):
                raise ValidationError("All selected resources must belong to the request FRT and Shift.")

    def _lock_resources(self):
        ids = self.request_line_ids.resource_id.ids
        if ids:
            self.env.cr.execute("SELECT id FROM gsus_resource WHERE id IN %s FOR UPDATE", [tuple(ids)])
            self.request_line_ids.resource_id.invalidate_recordset(["status", "reserved_request_id"])

    def action_submit(self):
        for record in self:
            if not record.request_line_ids: raise UserError("Select at least one available resource.")
            record._lock_resources()
            unavailable = record.request_line_ids.resource_id.filtered(lambda r: r.status != "available")
            if unavailable: raise UserError("These resources are no longer available: %s" % ", ".join(unavailable.mapped("display_name")))
            record.request_line_ids.resource_id.write({"status": "reserved", "reserved_request_id": record.id})
            record.state = "submitted"

    def action_review(self): self.write({"state": "review"})

    def action_approve(self):
        self.filtered(lambda r: r.state in ("submitted", "review")).write({"state": "approved", "approved_by": self.env.user.id, "approved_date": fields.Datetime.now()})

    def action_allocate(self):
        for record in self:
            record._lock_resources()
            invalid = record.request_line_ids.resource_id.filtered(lambda r: r.status != "reserved" or r.reserved_request_id != record)
            if invalid: raise UserError("Reservation is no longer valid for: %s" % ", ".join(invalid.mapped("display_name")))
            record.request_line_ids.resource_id.write({"status": "allocated"})
            record.state = "allocated"

    def action_deploy(self):
        for record in self:
            record._lock_resources()
            invalid = record.request_line_ids.resource_id.filtered(
                lambda r: r.status != "allocated" or r.reserved_request_id != record
            )
            if invalid:
                raise UserError("Only valid allocated resources can be deployed: %s" % ", ".join(invalid.mapped("display_name")))
            record.request_line_ids.resource_id.write({"status": "deployed"})
            record.state = "deployed"

    def _release_resources(self):
        for record in self:
            resources = record.request_line_ids.resource_id.filtered(lambda r: r.reserved_request_id == record and r.status in ("reserved", "allocated", "deployed"))
            resources.write({"status": "available", "reserved_request_id": False})

    def action_reject(self): self._release_resources(); self.write({"state": "rejected"})
    def action_cancel(self): self._release_resources(); self.write({"state": "cancelled"})
    def action_complete(self): self._release_resources(); self.write({"state": "completed"})


class GsusResourceRequestLine(models.Model):
    _name = "gsus.resource.request.line"
    _description = "GSUS Resource Request Line"

    request_id = fields.Many2one("gsus.resource.request", required=True, ondelete="cascade")
    resource_id = fields.Many2one("gsus.resource", required=True, ondelete="restrict", domain="[('status','=','available'),('location_id','=',parent.location_id),('shift_id','=',parent.shift_id)]")
    resource_type_id = fields.Many2one(related="resource_id.resource_type_id", store=True)
    quantity = fields.Integer(default=1, readonly=True)
    remarks = fields.Char()
    line_state = fields.Selection(related="resource_id.status", string="Resource Status")
    _request_resource_unique = models.Constraint("UNIQUE(request_id, resource_id)", "A resource can only be selected once per request.")
