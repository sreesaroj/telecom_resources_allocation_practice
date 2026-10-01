import base64

from odoo import fields, http
from odoo.exceptions import AccessDenied
from odoo.http import request
from odoo.tools.mimetypes import guess_mimetype


class GsusSimpleApi(http.Controller):
    """Simple portal-session API for the external customer screen."""

    _allowed_origins = {
        "https://resource-allocation.gsussoftwares.com",
    }

    def _cors_headers(self):
        origin = request.httprequest.headers.get("Origin")
        headers = {
            "Vary": "Origin",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Max-Age": "86400",
        }
        if origin in self._allowed_origins:
            headers["Access-Control-Allow-Origin"] = origin
        return headers

    def _json(self, data, status=200):
        response = request.make_json_response(data, status=status)
        response.headers.update(self._cors_headers())
        return response

    @http.route(
        [
            "/api/gsus/login",
            "/api/gsus/records",
            "/odoo/api/gsus/login",
            "/odoo/api/gsus/records",
            "/api/gsus/vehicle/<int:vehicle_id>/image",
            "/odoo/api/gsus/vehicle/<int:vehicle_id>/image",
            "/api/gsus/vehicle-km",
            "/odoo/api/gsus/vehicle-km",
        ],
        type="http",
        auth="public",
        methods=["OPTIONS"],
        csrf=False,
    )
    def api_options(self, **kwargs):
        response = request.make_response("", status=204)
        response.headers.update(self._cors_headers())
        return response

    def _customer_ids(self, user):
        Partner = request.env["res.partner"].sudo()
        if not user.share:
            return Partner.search([("is_telecom_customer", "=", True)]).ids
        return Partner.search([
            ("is_telecom_customer", "=", True),
            ("telecom_portal_user_ids", "in", user.id),
        ]).ids

    def _person_payload(self, person):
        phone = (
            person.personal_phone
            or person.work_phone
            or person.employee_id.mobile_phone
            or person.employee_id.work_phone
            or ""
        )
        return {
            "id": person.id,
            "employee_id": person.employee_id.id or False,
            "name": person.name,
            "phone": phone,
            "driving_license_number": person.driving_license_number if person.is_driver else False,
            "driving_license_expiry_date": (
                fields.Date.to_string(person.driving_license_expiry_date)
                if person.is_driver and person.driving_license_expiry_date else False
            ),
        }

    def _vehicle_payload(self, vehicle, km_entry=False):
        payload = {
            "id": vehicle.id,
            "number": vehicle.license_plate or "",
            "model": vehicle.model_id.display_name or "",
            "vehicle_type": vehicle.gsus_vehicle_type,
            "vehicle_type_label": dict(vehicle._fields["gsus_vehicle_type"].selection).get(
                vehicle.gsus_vehicle_type, ""
            ),
            "vendor_id": vehicle.gsus_vendor_id.id or False,
            "vendor": vehicle.gsus_vendor_id.name or "",
            "insurance_expiry_date": (
                fields.Date.to_string(vehicle.insurance_expiry_date)
                if vehicle.insurance_expiry_date else False
            ),
            "fc_date": fields.Date.to_string(vehicle.fc_date) if vehicle.fc_date else False,
            "image_url": (
                "/api/gsus/vehicle/%s/image" % vehicle.id
                if vehicle.image_1920 else False
            ),
        }
        # The customer portal opens vehicle details from the daily FRT record,
        # therefore expose that day's KM capture together with the vehicle.
        if km_entry:
            payload.update({
                "km_capture_id": km_entry.id,
                "starting_km": km_entry.starting_km,
                "ending_km": km_entry.ending_km,
                "total_km": km_entry.total_km,
                "replacement_km": km_entry.replacement_km,
                "operational_cumulative_km": km_entry.operational_km,
            })
        else:
            payload.update({
                "km_capture_id": False,
                "starting_km": False,
                "ending_km": False,
                "total_km": False,
                "replacement_km": False,
                "operational_cumulative_km": False,
            })
        return payload

    def _km_payload(self, entry):
        return {
            "id": entry.id,
            "vehicle": self._vehicle_payload(entry.vehicle_id),
            "is_alternate": entry.is_alternate,
            "starting_km": entry.starting_km,
            "ending_km": entry.ending_km,
            "total_km": entry.total_km,
        }

    @http.route(
        [
            "/api/gsus/vehicle/<int:vehicle_id>/image",
            "/odoo/api/gsus/vehicle/<int:vehicle_id>/image",
        ],
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def vehicle_image(self, vehicle_id, **kwargs):
        if not request.session.uid:
            return self._json({"success": False, "error": "Authentication required."}, 401)
        customer_ids = self._customer_ids(request.env.user)
        vehicle = request.env["fleet.vehicle"].sudo().browse(vehicle_id).exists()
        allowed = bool(vehicle and vehicle.gsus_customer_id.id in customer_ids)
        if vehicle and not allowed:
            allowed = bool(request.env["gsus.frt.availability.line"].sudo().search_count([
                ("customer_id", "in", customer_ids),
                "|",
                ("available_vehicle_ids", "in", vehicle.id),
                ("alternate_vehicle_ids", "in", vehicle.id),
            ]))
        if not allowed or not vehicle.image_1920:
            return self._json({"success": False, "error": "Vehicle image not found."}, 404)
        content = base64.b64decode(vehicle.image_1920)
        response = request.make_response(content, headers=[
            ("Content-Type", guess_mimetype(content)),
            ("Cache-Control", "private, max-age=3600"),
        ])
        response.headers.update(self._cors_headers())
        return response

    @http.route(
        ["/api/gsus/login", "/odoo/api/gsus/login"],
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def login(self, **kwargs):
        payload = request.httprequest.get_json(silent=True) or {}
        login, password = payload.get("login"), payload.get("password")
        if not login or not password:
            return self._json({"success": False, "error": "Login and password are required."}, 400)
        try:
            auth_info = request.session.authenticate(request.env, {
                "type": "password", "login": login, "password": password,
            })
        except AccessDenied:
            return self._json({"success": False, "error": "Invalid login or password."}, 401)
        user = request.env["res.users"].sudo().browse(auth_info["uid"])
        customers = request.env["res.partner"].sudo().browse(self._customer_ids(user)).exists()
        if not customers:
            request.session.logout()
            return self._json({"success": False, "error": "No telecom customer is assigned to this portal user."}, 403)
        return self._json({
            "success": True,
            "user": {"id": user.id, "name": user.name, "login": user.login},
            "customer": {"id": customers[0].id, "name": customers[0].name} if len(customers) == 1 else False,
        })

    @http.route(
        ["/api/gsus/records", "/odoo/api/gsus/records"],
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def records(self, **kwargs):
        if not request.session.uid:
            return self._json({"success": False, "error": "Authentication required."}, 401)
        customer_ids = self._customer_ids(request.env.user)
        if not customer_ids:
            return self._json({"success": False, "error": "No telecom customer is assigned."}, 403)
        sheets = request.env["gsus.frt.availability"].sudo().search([
            ("customer_id", "in", customer_ids),
        ], order="availability_date desc, customer_id, zone_id, location_id")
        records = []
        for sheet in sheets:
            km_by_vehicle_id = {
                entry.vehicle_id.id: entry for entry in sheet.vehicle_km_line_ids
            }
            resource_lines = []
            for line in sheet.line_ids:
                resource_lines.append({
                    "resource_type_id": line.resource_type_id.id,
                    "resource_type": line.resource_type_id.name,
                    "scope": line.scope_count,
                    "available": line.available_count,
                    "alternate": line.alternate_count,
                    "total_available": line.total_available,
                    "gap": line.gap_count,
                    "available_persons": [self._person_payload(person) for person in line.available_resource_ids],
                    "alternate_persons": [self._person_payload(person) for person in line.alternate_resource_ids],
                    "not_available_persons": [self._person_payload(person) for person in line.unavailable_resource_ids],
                    "available_vehicles": [
                        self._vehicle_payload(vehicle, km_by_vehicle_id.get(vehicle.id))
                        for vehicle in line.available_vehicle_ids
                    ],
                    "alternate_vehicles": [
                        self._vehicle_payload(vehicle, km_by_vehicle_id.get(vehicle.id))
                        for vehicle in line.alternate_vehicle_ids
                    ],
                    "remarks": line.remarks or "",
                })
            records.append({
                "id": sheet.id,
                "date": fields.Date.to_string(sheet.availability_date),
                "customer_id": sheet.customer_id.id,
                "customer": sheet.customer_id.name,
                "project_id": sheet.project_id.id,
                "project": sheet.project_id.name,
                "project_hour_format": sheet.project_id.gsus_hour_format,
                "project_hour_format_label": dict(
                    sheet.project_id._fields["gsus_hour_format"].selection
                ).get(sheet.project_id.gsus_hour_format, ""),
                "zone_id": sheet.zone_id.id,
                "zone": sheet.zone_id.name,
                "frt_id": sheet.location_id.id,
                "frt": sheet.location_id.name,
                "team_check_in": fields.Datetime.to_string(sheet.team_check_in) if sheet.team_check_in else False,
                "team_check_out": fields.Datetime.to_string(sheet.team_check_out) if sheet.team_check_out else False,
                "first_shift_check_in": fields.Datetime.to_string(sheet.first_shift_check_in) if sheet.first_shift_check_in else False,
                "first_shift_check_out": fields.Datetime.to_string(sheet.first_shift_check_out) if sheet.first_shift_check_out else False,
                "second_shift_check_in": fields.Datetime.to_string(sheet.second_shift_check_in) if sheet.second_shift_check_in else False,
                "second_shift_check_out": fields.Datetime.to_string(sheet.second_shift_check_out) if sheet.second_shift_check_out else False,
                "total_scope": sheet.total_scope,
                "total_available": sheet.total_available,
                "total_gap": sheet.total_gap,
                "readiness_percent": round(sheet.readiness_percent, 2),
                "resources": resource_lines,
                "vehicle_km": [self._km_payload(entry) for entry in sheet.vehicle_km_line_ids],
            })
        customers = request.env["res.partner"].sudo().browse(customer_ids).exists()
        return self._json({
            "success": True,
            "count": len(records),
            "customer": {"id": customers[0].id, "name": customers[0].name} if len(customers) == 1 else False,
            "records": records,
        })

    @http.route(
        ["/api/gsus/vehicle-km", "/odoo/api/gsus/vehicle-km"],
        type="http", auth="public", methods=["GET"], csrf=False,
    )
    def vehicle_km(self, from_date=None, to_date=None, location_id=None, **kwargs):
        """Vehicle KM report for the signed-in customer's portal dashboard.

        Optional query parameters: from_date, to_date (YYYY-MM-DD), location_id.
        """
        if not request.session.uid:
            return self._json({"success": False, "error": "Authentication required."}, 401)
        customer_ids = self._customer_ids(request.env.user)
        if not customer_ids:
            return self._json({"success": False, "error": "No telecom customer is assigned."}, 403)
        domain = [("customer_id", "in", customer_ids)]
        try:
            if from_date:
                domain.append(("availability_date", ">=", fields.Date.to_date(from_date)))
            if to_date:
                domain.append(("availability_date", "<=", fields.Date.to_date(to_date)))
            if location_id:
                domain.append(("location_id", "=", int(location_id)))
        except (TypeError, ValueError):
            return self._json({"success": False, "error": "Use dates in YYYY-MM-DD format."}, 400)
        entries = request.env["gsus.frt.vehicle.km"].sudo().search(
            domain, order="availability_date desc, location_id, vehicle_id"
        )
        location_totals = {}
        for entry in entries:
            key = entry.location_id.id
            value = location_totals.setdefault(key, {
                "frt_id": key, "frt": entry.location_id.name,
                "total_km": 0, "vehicle_count": set(),
            })
            value["total_km"] += entry.total_km
            value["vehicle_count"].add(entry.vehicle_id.id)
        totals = [{
            **value, "total_km": round(value["total_km"], 2),
            "vehicle_count": len(value["vehicle_count"]),
        } for value in location_totals.values()]
        return self._json({
            "success": True, "count": len(entries),
            "from_date": from_date or False, "to_date": to_date or False,
            "location_id": int(location_id) if location_id else False,
            "total_km": round(sum(entries.mapped("total_km")), 2),
            "location_totals": totals,
            "records": [{
                "date": fields.Date.to_string(entry.availability_date),
                "customer": entry.customer_id.name,
                "project": entry.project_id.name,
                "zone": entry.zone_id.name,
                "frt": entry.location_id.name,
                **self._km_payload(entry),
            } for entry in entries],
        })
