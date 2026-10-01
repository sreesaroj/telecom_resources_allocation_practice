from io import BytesIO
from datetime import date
from urllib.parse import urlencode

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from odoo import fields, http, Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal


class GsusPortal(CustomerPortal):
    def _gsus_is_restricted_rtm(self):
        """RTM Managers must never get the all-company dashboard sudo scope."""
        user = request.env.user
        return user.id != 1 and user.has_group("gsus_resource_allocation.group_gsus_rtm_manager")

    def _gsus_location_scope_domain(self):
        if not self._gsus_is_restricted_rtm():
            return []
        return ["|", ("project_id.rtm_manager_id", "=", request.env.user.id), ("rtm_manager_id", "=", request.env.user.id)]

    def _gsus_availability_scope_domain(self):
        if not self._gsus_is_restricted_rtm():
            return []
        return ["|", ("project_id.rtm_manager_id", "=", request.env.user.id), ("location_id.rtm_manager_id", "=", request.env.user.id)]

    def _gsus_allowed_customer_ids(self):
        """Internal users see all customers; portal users see explicit assignments only."""
        Partner = request.env["res.partner"].sudo()
        if self._gsus_is_restricted_rtm():
            return request.env["gsus.location"].sudo().search(
                self._gsus_location_scope_domain()
            ).mapped("customer_id").ids
        if not request.env.user.share:
            return Partner.search([("is_telecom_customer", "=", True)]).ids
        return Partner.search([
            ("is_telecom_customer", "=", True),
            ("telecom_portal_user_ids", "in", request.env.user.id),
        ]).ids

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "gsus_location_count" in counters:
            customer_ids = self._gsus_allowed_customer_ids()
            values["gsus_location_count"] = request.env["gsus.location"].sudo().search_count([
                ("customer_id", "in", customer_ids)
            ])
        if "gsus_request_count" in counters:
            values["gsus_request_count"] = request.env["gsus.resource.request"].search_count([])
        return values

    def _gsus_dashboard_filters(self, kwargs):
        allowed_customer_ids = self._gsus_allowed_customer_ids()
        domain = [("customer_id", "in", allowed_customer_ids)]
        domain += self._gsus_availability_scope_domain()
        selected_date = kwargs.get("date") or ""
        try:
            parsed_date = fields.Date.to_date(selected_date) if selected_date else False
        except (TypeError, ValueError):
            parsed_date = False
            selected_date = ""
        if parsed_date:
            domain.append(("availability_date", "=", parsed_date))

        selected_month = kwargs.get("month") or ""
        selected_from_date = kwargs.get("from_date") or ""
        selected_to_date = kwargs.get("to_date") or ""
        try:
            if selected_month:
                year, month = [int(value) for value in selected_month.split("-", 1)]
                period_from = date(year, month, 1)
                period_to = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
                period_to = period_to.fromordinal(period_to.toordinal() - 1)
            else:
                period_from = fields.Date.to_date(selected_from_date) if selected_from_date else False
                period_to = fields.Date.to_date(selected_to_date) if selected_to_date else False
        except (TypeError, ValueError):
            selected_month = selected_from_date = selected_to_date = ""
            period_from = period_to = False
        if period_from:
            domain.append(("availability_date", ">=", period_from))
        if period_to:
            domain.append(("availability_date", "<=", period_to))

        def valid_id(value):
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        selected_customer_id = valid_id(kwargs.get("customer_id"))
        if selected_customer_id not in allowed_customer_ids:
            selected_customer_id = 0
        if selected_customer_id:
            domain.append(("customer_id", "=", selected_customer_id))

        Zone = request.env["gsus.zone"].sudo()
        zone_domain = [("customer_id", "in", allowed_customer_ids)]
        if selected_customer_id:
            zone_domain.append(("customer_id", "=", selected_customer_id))
        if self._gsus_is_restricted_rtm():
            zone_domain += ["|", ("project_id.rtm_manager_id", "=", request.env.user.id), ("location_ids.rtm_manager_id", "=", request.env.user.id)]
        all_zones = Zone.search(zone_domain, order="name")
        selected_zone_id = valid_id(kwargs.get("zone_id"))
        selected_zone = all_zones.filtered(lambda zone: zone.id == selected_zone_id)
        if selected_zone and (not selected_customer_id or selected_zone.customer_id.id == selected_customer_id):
            domain.append(("zone_id", "=", selected_zone_id))
        else:
            selected_zone_id = 0
        query = {key: value for key, value in {
            "date": selected_date, "month": selected_month, "from_date": selected_from_date,
            "to_date": selected_to_date, "customer_id": selected_customer_id, "zone_id": selected_zone_id,
        }.items() if value}
        return domain, {
            "filter_customers": request.env["res.partner"].sudo().browse(allowed_customer_ids).exists(),
            "filter_zones": all_zones,
            "selected_date": selected_date,
            "selected_month": selected_month,
            "selected_from_date": selected_from_date,
            "selected_to_date": selected_to_date,
            "period_from": period_from,
            "period_to": period_to,
            "selected_customer_id": selected_customer_id,
            "selected_zone_id": selected_zone_id,
            "export_url": "/gsus/export/xlsx" + (("?" + urlencode(query)) if query else ""),
            "report_query": urlencode(query),
        }

    @http.route(["/my/gsus"], type="http", auth="user", website=True)
    def portal_gsus_dashboard(self, **kwargs):
        domain, filter_values = self._gsus_dashboard_filters(kwargs)
        customers = filter_values["filter_customers"]
        sheets = request.env["gsus.frt.availability"].sudo().search(domain, order="availability_date, zone_id, location_id")
        gaps = sheets.line_ids.filtered(lambda line: line.gap_count > 0)
        projects = sheets.mapped("project_id")
        zones = sheets.mapped("zone_id")
        vehicles = (
            sheets.line_ids.mapped("available_vehicle_ids")
            | sheets.line_ids.mapped("alternate_vehicle_ids")
        )
        km_entries = sheets.mapped("vehicle_km_line_ids")
        alternate_entries = sheets.mapped("alternate_allocation_ids")
        values = {
            "page_name": "gsus_simple_dashboard",
            "customers": customers,
            "projects": projects,
            "zones": zones,
            "vehicles": vehicles,
            "sheets": sheets,
            "gaps": gaps,
            "total_scope": sum(sheets.mapped("total_scope")),
            "total_available": sum(sheets.line_ids.mapped("available_count")),
            "total_alternate": sum(sheets.line_ids.mapped("alternate_count")),
            "total_gap": sum(sheets.mapped("total_gap")),
            "total_vehicle_km": round(sum(km_entries.mapped("total_km")), 2),
            "vehicle_km_entry_count": len(km_entries),
            "temporary_alternate_count": len(alternate_entries),
            "is_internal": not request.env.user.share,
            "backend_url": "/odoo/action-%s" % request.env.ref(
                "gsus_resource_allocation.action_gsus_frt_availability"
            ).id,
            "vehicle_km_dashboard_url": "/gsus/dashboard/vehicle-km" + (
                "?" + filter_values["report_query"] if filter_values["report_query"] else ""
            ),
            "alternate_dashboard_url": "/gsus/dashboard/temporary-alternates" + (
                "?" + filter_values["report_query"] if filter_values["report_query"] else ""
            ),
        }
        values.update(filter_values)
        return request.render("gsus_resource_allocation.portal_gsus_simple_dashboard", values)

    def _gsus_operations_report_values(self, report_kind, kwargs):
        domain, filter_values = self._gsus_dashboard_filters(kwargs)
        sheets = request.env["gsus.frt.availability"].sudo().search(
            domain, order="availability_date desc, zone_id, location_id"
        )
        all_km_entries = sheets.mapped("vehicle_km_line_ids")
        all_alternate_entries = sheets.mapped("alternate_allocation_ids")

        def selected_id(name):
            try:
                return int(kwargs.get(name) or 0)
            except (TypeError, ValueError):
                return 0

        selected_vehicle_id = selected_id("vehicle_id")
        selected_location_id = selected_id("location_id")
        selected_employee_id = selected_id("employee_id")
        filter_vehicles = all_km_entries.mapped("vehicle_id").sorted("display_name")
        # Locations come from the filtered availability sheets, not just from
        # stored KM related fields (older KM rows can have those fields empty).
        filter_locations = (sheets.mapped("location_id") | all_alternate_entries.mapped("location_id")).sorted("name")
        filter_employees = (
            all_alternate_entries.mapped("original_resource_id")
            | all_alternate_entries.mapped("alternate_resource_id")
        ).sorted("name")
        if selected_vehicle_id not in filter_vehicles.ids:
            selected_vehicle_id = 0
        if selected_location_id not in filter_locations.ids:
            selected_location_id = 0
        if selected_employee_id not in filter_employees.ids:
            selected_employee_id = 0

        km_entries = all_km_entries.filtered(
            lambda entry: (not selected_vehicle_id or entry.vehicle_id.id == selected_vehicle_id)
            and (not selected_location_id or entry.location_id.id == selected_location_id)
        ).sorted(
            key=lambda entry: (entry.availability_date or fields.Date.to_date("1900-01-01"), entry.location_id.name or "", entry.vehicle_id.display_name),
            reverse=True,
        )
        alternate_entries = all_alternate_entries.filtered(
            lambda entry: (not selected_location_id or entry.location_id.id == selected_location_id)
            and (
                not selected_employee_id
                or entry.original_resource_id.id == selected_employee_id
                or entry.alternate_resource_id.id == selected_employee_id
            )
        ).sorted(
            key=lambda entry: (entry.availability_date or fields.Date.to_date("1900-01-01"), entry.location_id.name or ""),
            reverse=True,
        )
        location_summary = {}
        vehicle_summary = {}
        location_domain = [
            ("customer_id", "in", self._gsus_allowed_customer_ids()),
            ("active", "=", True),
        ] + self._gsus_location_scope_domain()
        scope_locations = request.env["gsus.location"].sudo().search(location_domain)
        if selected_location_id:
            scope_locations = scope_locations.filtered(lambda location: location.id == selected_location_id)
        elif filter_values["selected_zone_id"]:
            scope_locations = scope_locations.filtered(
                lambda location: location.zone_id.id == filter_values["selected_zone_id"]
            )
        elif filter_values["selected_customer_id"]:
            scope_locations = scope_locations.filtered(lambda location: location.customer_id.id == filter_values["selected_customer_id"])
        for location in scope_locations:
            location_summary[location.id] = {
                "name": location.name, "zone": location.zone_id.name, "total_km": 0.0,
                "scope_km": location.monthly_km_scope, "vehicles": set(), "entries": 0,
            }
        for entry in km_entries:
            location = location_summary.setdefault(entry.location_id.id, {
                "name": entry.location_id.name,
                "zone": entry.zone_id.name,
                "total_km": 0.0,
                "scope_km": entry.location_id.monthly_km_scope,
                "vehicles": set(),
                "entries": 0,
            })
            location["total_km"] += entry.total_km
            location["vehicles"].add(entry.vehicle_id.id)
            location["entries"] += 1
            vehicle = vehicle_summary.setdefault(entry.vehicle_id.id, {
                "name": entry.vehicle_id.display_name,
                "number": entry.vehicle_number or "—",
                "own_km": 0.0,
                "replacement_km": 0.0,
                "operational_km": 0.0,
                "entries": 0,
            })
            vehicle["own_km"] += entry.total_km
            vehicle["replacement_km"] += entry.replacement_km
            vehicle["operational_km"] += entry.operational_km
            vehicle["entries"] += 1
        for summary in location_summary.values():
            summary["vehicle_count"] = len(summary.pop("vehicles"))
            summary["total_km"] = round(summary["total_km"], 2)
            summary["scope_km"] = round(summary["scope_km"], 2)
            summary["under_km"] = round(max(summary["scope_km"] - summary["total_km"], 0), 2)
            summary["over_km"] = round(max(summary["total_km"] - summary["scope_km"], 0), 2)
        for summary in vehicle_summary.values():
            summary["own_km"] = round(summary["own_km"], 2)
            summary["replacement_km"] = round(summary["replacement_km"], 2)
            summary["operational_km"] = round(summary["operational_km"], 2)
        values = {
            "page_name": "gsus_operations_report",
            "report_kind": report_kind,
            "sheets": sheets,
            "km_entries": km_entries,
            "alternate_entries": alternate_entries,
            "location_summaries": sorted(
                location_summary.values(), key=lambda item: item["total_km"], reverse=True
            ),
            "vehicle_summaries": sorted(
                vehicle_summary.values(), key=lambda item: item["operational_km"], reverse=True
            ),
            "total_vehicle_km": round(sum(km_entries.mapped("total_km")), 2),
            "total_vehicle_count": len(km_entries.mapped("vehicle_id")),
            "total_location_count": len(km_entries.mapped("location_id")),
            "total_scope_km": round(sum(item["scope_km"] for item in location_summary.values()), 2),
            "total_under_km": round(sum(item["under_km"] for item in location_summary.values()), 2),
            "total_over_km": round(sum(item["over_km"] for item in location_summary.values()), 2),
            "alternate_count": len(alternate_entries),
            "filter_vehicles": filter_vehicles,
            "filter_locations": filter_locations,
            "filter_employees": filter_employees,
            "selected_vehicle_id": selected_vehicle_id,
            "selected_location_id": selected_location_id,
            "selected_employee_id": selected_employee_id,
            "dashboard_url": "/gsus/dashboard",
            "report_action": {
                "km_summary": "/gsus/dashboard/vehicle-km",
                "alternates": "/gsus/dashboard/temporary-alternates",
            }[report_kind],
        }
        values.update(filter_values)
        export_query = dict(kwargs)
        export_query.update({
            "vehicle_id": selected_vehicle_id or "",
            "location_id": selected_location_id or "",
            "employee_id": selected_employee_id or "",
        })
        export_query = {key: value for key, value in export_query.items() if value not in (None, "", False)}
        values["report_export_url"] = "/gsus/dashboard/export/%s.xlsx%s" % (
            "vehicle-km" if report_kind == "km_summary" else "temporary-alternates",
            ("?" + urlencode(export_query)) if export_query else "",
        )
        return values

    @http.route(["/gsus/dashboard/vehicle-km"], type="http", auth="user", website=True)
    def gsus_vehicle_km_dashboard(self, **kwargs):
        return request.render(
            "gsus_resource_allocation.portal_gsus_operations_report",
            self._gsus_operations_report_values("km_summary", kwargs),
        )

    @http.route(["/gsus/dashboard/temporary-alternates"], type="http", auth="user", website=True)
    def gsus_temporary_alternates_dashboard(self, **kwargs):
        return request.render(
            "gsus_resource_allocation.portal_gsus_operations_report",
            self._gsus_operations_report_values("alternates", kwargs),
        )

    def _operations_xlsx_response(self, report_kind, kwargs):
        values = self._gsus_operations_report_values(report_kind, kwargs)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Vehicle KM" if report_kind == "km_summary" else "Temporary Alternates"
        title = "GSUS Vehicle KM Details" if report_kind == "km_summary" else "GSUS Temporary Alternate Details"
        headers = (
            ["Date", "Customer", "Project", "Zone", "FRT Location", "Vehicle", "Vehicle Number", "Alternate Vehicle", "Replacing Vehicle", "Starting KM", "Ending KM", "Total KM", "Replacement KM", "Operational KM"]
            if report_kind == "km_summary" else
            ["Date", "Customer", "Project", "Zone", "FRT Location", "Resource Type", "Original Employee", "Temporary Alternate", "Remarks"]
        )
        sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
        sheet.cell(1, 1, title)
        sheet.cell(1, 1).fill = PatternFill("solid", fgColor="17365D")
        sheet.cell(1, 1).font = Font(color="FFFFFF", bold=True)
        sheet.cell(1, 1).alignment = Alignment(horizontal="center")
        for column, header in enumerate(headers, 1):
            cell = sheet.cell(3, column, header)
            cell.fill = PatternFill("solid", fgColor="D9EAF7")
            cell.font = Font(bold=True)
        if report_kind == "km_summary":
            for row, entry in enumerate(values["km_entries"], 4):
                replacing = entry.replaces_km_id.vehicle_id.display_name if entry.replaces_km_id else ""
                row_values = [
                    entry.availability_id.availability_date, entry.availability_id.customer_id.name,
                    entry.availability_id.project_id.name, entry.availability_id.zone_id.name,
                    entry.availability_id.location_id.name, entry.vehicle_id.display_name, entry.vehicle_number or "", "Yes" if entry.is_alternate else "No",
                    replacing, entry.starting_km, entry.ending_km, entry.total_km, entry.replacement_km, entry.operational_km,
                ]
                for column, value in enumerate(row_values, 1):
                    sheet.cell(row, column, value)
        else:
            for row, entry in enumerate(values["alternate_entries"], 4):
                row_values = [
                    entry.availability_date, entry.customer_id.name, entry.project_id.name, entry.zone_id.name,
                    entry.location_id.name, entry.resource_type_id.name, entry.original_resource_id.name or "",
                    entry.alternate_resource_id.name, entry.remarks or "",
                ]
                for column, value in enumerate(row_values, 1):
                    sheet.cell(row, column, value)
        for column in range(1, len(headers) + 1):
            sheet.column_dimensions[chr(64 + column)].width = 20
        sheet.freeze_panes = "A4"
        output = BytesIO()
        workbook.save(output)
        filename = "GSUS_Vehicle_KM_Details.xlsx" if report_kind == "km_summary" else "GSUS_Temporary_Alternates.xlsx"
        return request.make_response(output.getvalue(), headers=[
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", 'attachment; filename="%s"' % filename),
        ])

    @http.route(["/gsus/dashboard/export/vehicle-km.xlsx"], type="http", auth="user", website=True)
    def gsus_export_vehicle_km_xlsx(self, **kwargs):
        return self._operations_xlsx_response("km_summary", kwargs)

    @http.route(["/gsus/dashboard/export/temporary-alternates.xlsx"], type="http", auth="user", website=True)
    def gsus_export_temporary_alternates_xlsx(self, **kwargs):
        return self._operations_xlsx_response("alternates", kwargs)

    @http.route(["/gsus/export/xlsx"], type="http", auth="user", website=True)
    def gsus_export_xlsx(self, **kwargs):
        domain, filter_values = self._gsus_dashboard_filters(kwargs)
        sheets = request.env["gsus.frt.availability"].sudo().search(domain, order="availability_date, zone_id, location_id")
        gaps = sheets.line_ids.filtered(lambda line: line.gap_count > 0)
        workbook = Workbook()
        ws1 = workbook.active
        ws1.title = "Sheet1"
        ws2 = workbook.create_sheet("Sheet2")
        navy = PatternFill("solid", fgColor="17365D")
        blue = PatternFill("solid", fgColor="D9EAF7")
        white_font = Font(color="FFFFFF", bold=True)
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        types = request.env["gsus.resource.type"].sudo().search([
            ("code", "in", ["VEHICLE", "HELPER", "PATROLLER", "DRIVER", "SPLICER", "ENGINEER"])
        ], order="sequence, name")

        ws1.merge_cells(start_row=1, start_column=1, end_row=1, end_column=2 + len(types) * 7)
        title_date = filter_values["selected_date"] or "All Dates"
        ws1.cell(1, 1, "GSUS Resources Availability - %s" % title_date)
        ws1.cell(1, 1).fill = navy
        ws1.cell(1, 1).font = white_font
        ws1.cell(1, 1).alignment = center
        ws1.cell(2, 1, "Zone")
        ws1.cell(2, 2, "FRT")
        column = 3
        for resource_type in types:
            ws1.merge_cells(start_row=2, start_column=column, end_row=2, end_column=column + 6)
            ws1.cell(2, column, resource_type.name)
            for offset, title in enumerate(["Scope", "Available", "Gap", "Available Names", "Temp / Alternate Count", "Temp / Alternate Names", "Remarks"]):
                ws1.cell(3, column + offset, title)
            column += 7
        ws1.merge_cells(start_row=2, start_column=1, end_row=3, end_column=1)
        ws1.merge_cells(start_row=2, start_column=2, end_row=3, end_column=2)
        for row in ws1.iter_rows(min_row=2, max_row=3, max_col=2 + len(types) * 7):
            for cell in row:
                cell.fill = blue
                cell.font = Font(bold=True)
                cell.alignment = center
        for row_number, sheet in enumerate(sheets, 4):
            ws1.cell(row_number, 1, sheet.zone_id.name)
            ws1.cell(row_number, 2, sheet.location_id.name)
            column = 3
            for resource_type in types:
                line = sheet.line_ids.filtered(lambda item: item.resource_type_id == resource_type)[:1]
                names = line.available_vehicle_ids.mapped("license_plate") if resource_type.category == "vehicle" else line.available_resource_ids.mapped("name")
                alternate_names = (
                    line.alternate_vehicle_ids.mapped("license_plate")
                    if resource_type.category == "vehicle"
                    else line.alternate_resource_ids.mapped("name")
                )
                values = [
                    line.scope_count if line else 0,
                    line.available_count if line else 0,
                    line.gap_count if line else 0,
                    ", ".join(names),
                    line.alternate_count if line else 0,
                    ", ".join(alternate_names) if line else "",
                    line.remarks or "" if line else "",
                ]
                for offset, value in enumerate(values):
                    ws1.cell(row_number, column + offset, value)
                column += 7
        ws1.freeze_panes = "C4"
        ws1.column_dimensions["A"].width = 20
        ws1.column_dimensions["B"].width = 22

        ws2.cell(1, 1, "GSUS Resources Gap Summary")
        ws2.cell(1, 1).fill = navy
        ws2.cell(1, 1).font = white_font
        ws2.cell(3, 1, "Zone")
        for index, resource_type in enumerate(types, 2):
            ws2.cell(3, index, resource_type.name)
        ws2.cell(3, 2 + len(types), "Grand Total")
        for cell in ws2[3]:
            cell.fill = blue
            cell.font = Font(bold=True)
        zones = sheets.mapped("zone_id")
        for row_number, zone in enumerate(zones, 4):
            ws2.cell(row_number, 1, zone.name)
            total = 0
            zone_lines = sheets.filtered(lambda item: item.zone_id == zone).line_ids
            for index, resource_type in enumerate(types, 2):
                gap = sum(zone_lines.filtered(lambda item: item.resource_type_id == resource_type).mapped("gap_count"))
                ws2.cell(row_number, index, gap)
                total += gap
            ws2.cell(row_number, 2 + len(types), total)
        detail_row = 5 + len(zones)
        headers = ["S.No", "Description", "FRT", "Available / Missing Names", "Gap", "Aging Days", "Remarks", "Expected Closure", "Responsible"]
        for index, title in enumerate(headers, 1):
            ws2.cell(detail_row, index, title)
            ws2.cell(detail_row, index).fill = blue
            ws2.cell(detail_row, index).font = Font(bold=True)
        for serial, line in enumerate(gaps, 1):
            row_number = detail_row + serial
            names = line.available_vehicle_ids.mapped("license_plate") if line.resource_category == "vehicle" else line.available_resource_ids.mapped("name")
            values = [serial, line.resource_type_id.name, line.location_id.name, ", ".join(names), line.gap_count,
                      line.aging_days, line.remarks or "", line.expected_closure_date or "", line.responsible_id.name or ""]
            for index, value in enumerate(values, 1):
                ws2.cell(row_number, index, value)
        ws2.freeze_panes = "B4"
        ws2.column_dimensions["A"].width = 16
        ws2.column_dimensions["B"].width = 22
        ws2.column_dimensions["C"].width = 22

        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return request.make_response(output.getvalue(), headers=[
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", 'attachment; filename="GSUS_Resource_Availability.xlsx"'),
        ])

    @http.route(["/my/gsus/location/<int:location_id>"], type="http", auth="user", website=True)
    def portal_gsus_location(self, location_id, **kwargs):
        location = self._document_check_access("gsus.location", location_id)
        latest = request.env["gsus.availability"].search([
            ("location_id", "=", location.id), ("state", "in", ["submitted", "approved"])
        ], order="availability_date desc, id desc", limit=1)
        gaps = request.env["gsus.gap"].search([("location_id", "=", location.id), ("state", "!=", "resolved")])
        # The location has already passed the portal record-rule check. Sudo is
        # limited to this location so the page can show safe worker names/roles
        # without exposing the worker model or private fields through portal RPC.
        assignments = request.env["gsus.assignment"].sudo().search([("location_id", "=", location.id), ("state", "=", "active")])
        return request.render("gsus_resource_allocation.portal_gsus_location", {
            "page_name": "gsus_location", "location": location, "latest": latest,
            "gaps": gaps, "assignments": assignments,
        })

    @http.route(["/gsus/dashboard"], type="http", auth="user", website=True)
    def internal_gsus_dashboard(self, **kwargs):
        return self.portal_gsus_dashboard(**kwargs)

    def _allowed_project_ids(self):
        return request.env.user.partner_id.allowed_project_ids.ids

    @http.route(["/my/gsus/resources"], type="http", auth="user", website=True)
    def portal_available_resources(self, project_id=None, service_category_id=None, zone_id=None,
                                   location_id=None, shift_id=None, resource_type_id=None, search=None, **kwargs):
        allowed_projects = self._allowed_project_ids()
        domain = [("project_id", "in", allowed_projects), ("status", "=", "available")]
        if project_id and int(project_id) in allowed_projects: domain.append(("project_id", "=", int(project_id)))
        if service_category_id: domain.append(("service_category_id", "=", int(service_category_id)))
        if zone_id: domain.append(("zone_id", "=", int(zone_id)))
        if location_id: domain.append(("location_id", "=", int(location_id)))
        if shift_id: domain.append(("shift_id", "=", int(shift_id)))
        if resource_type_id: domain.append(("resource_type_id", "=", int(resource_type_id)))
        if search:
            domain += ["|", "|", ("name", "ilike", search), ("code", "ilike", search), ("location_id.name", "ilike", search)]
        resources = request.env["gsus.resource"].sudo().search(domain)
        projects = request.env["project.project"].sudo().browse(allowed_projects).exists()
        categories = request.env["gsus.service.category"].sudo().search([])
        zones = request.env["gsus.zone"].sudo().search([("project_id", "in", allowed_projects)])
        locations = request.env["gsus.location"].sudo().search([("project_id", "in", allowed_projects)])
        shifts = request.env["gsus.shift"].sudo().search([])
        resource_types = request.env["gsus.resource.type"].sudo().search([])
        return request.render("gsus_resource_allocation.portal_available_resources", {
            "page_name": "gsus_resources", "resources": resources, "projects": projects,
            "categories": categories, "zones": zones, "locations": locations,
            "shifts": shifts, "resource_types": resource_types,
            "selected_project": int(project_id) if project_id else False,
            "selected_category": int(service_category_id) if service_category_id else False,
            "selected_zone": int(zone_id) if zone_id else False,
            "selected_location": int(location_id) if location_id else False,
            "selected_shift": int(shift_id) if shift_id else False,
            "selected_type": int(resource_type_id) if resource_type_id else False,
            "search_term": search or "",
        })

    @http.route(["/my/gsus/request/create"], type="http", auth="user", methods=["POST"], website=True)
    def portal_create_resource_request(self, **post):
        try:
            ids = [int(value) for value in request.httprequest.form.getlist("resource_ids")]
            if not ids: raise UserError("Select at least one resource.")
            allowed_projects = self._allowed_project_ids()
            resources = request.env["gsus.resource"].sudo().search([
                ("id", "in", ids), ("project_id", "in", allowed_projects), ("status", "=", "available")
            ])
            if len(resources) != len(set(ids)): raise AccessError("One or more selected resources are unavailable or outside your permitted projects.")
            first = resources[0]
            if any(r.location_id != first.location_id or r.shift_id != first.shift_id for r in resources):
                raise ValidationError("Select resources from one FRT and one Shift per request.")
            resource_request = request.env["gsus.resource.request"].sudo().create({
                "vendor_id": request.env.user.partner_id.id, "customer_id": first.customer_id.id,
                "project_id": first.project_id.id, "zone_id": first.zone_id.id,
                "service_category_id": first.service_category_id.id,
                "location_id": first.location_id.id, "shift_id": first.shift_id.id,
                "required_from": fields.Datetime.to_datetime(post.get("required_from")),
                "required_to": fields.Datetime.to_datetime(post.get("required_to")),
                "notes": post.get("notes"),
                "request_line_ids": [Command.create({"resource_id": resource.id}) for resource in resources],
            })
            resource_request.action_submit()
            return request.redirect(f"/my/gsus/requests/{resource_request.id}?created=1")
        except (AccessError, UserError, ValidationError, ValueError) as error:
            return request.redirect("/my/gsus/resources?error=%s" % str(error))

    @http.route(["/my/gsus/requests"], type="http", auth="user", website=True)
    def portal_resource_requests(self, **kwargs):
        records = request.env["gsus.resource.request"].search([]).sudo()
        return request.render("gsus_resource_allocation.portal_resource_requests", {"page_name": "gsus_requests", "requests": records})

    @http.route(["/my/gsus/requests/<int:request_id>"], type="http", auth="user", website=True)
    def portal_resource_request_detail(self, request_id, **kwargs):
        record = self._document_check_access("gsus.resource.request", request_id).sudo()
        return request.render("gsus_resource_allocation.portal_resource_request_detail", {
            "page_name": "gsus_request", "resource_request": record, "created": kwargs.get("created"),
        })
