from odoo import api, fields, models


class GsusFrtAvailabilityDemoSeed(models.Model):
    _inherit = "gsus.frt.availability"

    @api.model
    def action_sync_server_demo_data(self):
        """Create or update an idempotent demo dataset for manual server testing."""
        Partner = self.env["res.partner"].sudo()
        Project = self.env["project.project"].sudo()
        Zone = self.env["gsus.zone"].sudo()
        Location = self.env["gsus.location"].sudo()
        ResourceType = self.env["gsus.resource.type"].sudo()
        Requirement = self.env["gsus.requirement"].sudo()
        Resource = self.env["gsus.resource"].sudo()
        Vehicle = self.env["fleet.vehicle"].sudo()

        customer = Partner.search([
            ("is_telecom_customer", "=", True), ("name", "=", "Airtel Demo Telecom Services")
        ], limit=1)
        customer_values = {
            "name": "Airtel Demo Telecom Services", "is_company": True,
            "is_telecom_customer": True, "phone": "+91 44 5555 2026",
            "email": "server.demo@airtel.example",
        }
        if customer:
            customer.write(customer_values)
        else:
            customer = Partner.create(customer_values)

        project = Project.search([
            ("customer_id", "=", customer.id), ("name", "=", "Airtel Tamil Nadu FRT Demo 2026")
        ], limit=1)
        project_values = {
            "name": "Airtel Tamil Nadu FRT Demo 2026", "partner_id": customer.id,
            "customer_id": customer.id, "telecom_project_type": "frt_maintenance",
            "telecom_active": True,
        }
        if project:
            project.write(project_values)
        else:
            project = Project.create(project_values)

        resource_types = {}
        for name, code, category in [
            ("Vehicle", "VEHICLE", "vehicle"), ("Driver", "DRIVER", "person"),
            ("Helper", "HELPER", "person"), ("Patroller", "PATROLLER", "person"),
        ]:
            resource_type = ResourceType.search([("code", "=", code)], limit=1)
            if not resource_type:
                resource_type = ResourceType.create({"name": name, "code": code, "category": category})
            resource_types[code] = resource_type

        day_shift = self.env.ref("gsus_resource_allocation.shift_day_12")
        night_shift = self.env.ref("gsus_resource_allocation.shift_night_12")
        today = fields.Date.context_today(self)
        structure = {
            "Airtel Demo Chennai": ["Airtel Anna Nagar FRT", "Airtel OMR FRT"],
            "Airtel Demo South": ["Airtel Tambaram FRT", "Airtel Chengalpattu FRT"],
        }
        scope = {"VEHICLE": 1, "DRIVER": 2, "HELPER": 4, "PATROLLER": 2}
        created_locations = self.env["gsus.location"]

        for zone_index, (zone_name, location_names) in enumerate(structure.items(), 1):
            zone_code = "SRV-AIR-Z%02d" % zone_index
            zone = Zone.search([("code", "=", zone_code)], limit=1)
            zone_values = {"name": zone_name, "code": zone_code, "project_id": project.id, "active": True}
            if zone:
                zone.write(zone_values)
            else:
                zone = Zone.create(zone_values)

            for location_index, location_name in enumerate(location_names, 1):
                location_code = "%s-F%02d" % (zone_code, location_index)
                location = Location.search([("code", "=", location_code)], limit=1)
                location_values = {
                    "name": location_name, "code": location_code, "zone_id": zone.id,
                    "responsible_id": self.env.user.id, "active": True,
                }
                if location:
                    location.write(location_values)
                else:
                    location = Location.create(location_values)
                created_locations |= location

                for type_code, required_count in scope.items():
                    resource_type = resource_types[type_code]
                    requirement = Requirement.search([
                        ("location_id", "=", location.id),
                        ("resource_type_id", "=", resource_type.id),
                    ], limit=1)
                    values = {
                        "location_id": location.id, "resource_type_id": resource_type.id,
                        "required_count": required_count, "notes": "Server scheduled-action demo scope",
                        "active": True,
                    }
                    if requirement:
                        requirement.write(values)
                    else:
                        requirement = Requirement.create(values)

                    persons = Resource
                    vehicles = Vehicle
                    available_count = required_count if type_code != "HELPER" else required_count - 1
                    if type_code == "VEHICLE":
                        plate = "TN-SRV-%02d%02d" % (zone_index, location_index)
                        vehicle = Vehicle.search([("license_plate", "=", plate)], limit=1)
                        vehicle_values = {
                            "license_plate": plate, "gsus_customer_id": customer.id,
                            "gsus_project_id": project.id, "gsus_zone_id": zone.id,
                            "gsus_location_id": location.id, "gsus_status": "available",
                            "insurance_policy_no": "SRV-POL-%02d%02d" % (zone_index, location_index),
                            "insurance_company": "Server Demo Insurance",
                            "insurance_expiry_date": "2027-12-31", "permit_expiry_date": "2027-12-31",
                        }
                        if vehicle:
                            vehicle.write(vehicle_values)
                        else:
                            vehicle = Vehicle.create(vehicle_values)
                        vehicles = vehicle
                    else:
                        persons = Resource.browse()
                        for number in range(1, available_count + 1):
                            code = "SRV-%s-%02d%02d-%02d" % (type_code, zone_index, location_index, number)
                            person = Resource.search([("code", "=", code)], limit=1)
                            person_values = {
                                "name": "%s %s %02d" % (location_name, resource_type.name, number),
                                "code": code, "resource_type_id": resource_type.id,
                                "ownership": "gsus_own", "customer_id": customer.id,
                                "project_id": project.id, "zone_id": zone.id, "location_id": location.id,
                                "shift_id": day_shift.id if number % 2 else night_shift.id,
                                "status": "available", "remarks": "Server scheduled-action demo person",
                            }
                            if person:
                                person.write(person_values)
                            else:
                                person = Resource.create(person_values)
                            persons |= person
                    requirement.write({
                        "assigned_resource_ids": [(6, 0, persons.ids)],
                        "assigned_vehicle_ids": [(6, 0, vehicles.ids)],
                    })

                availability = self.search([
                    ("location_id", "=", location.id), ("availability_date", "=", today)
                ], limit=1)
                availability_values = {
                    "customer_id": customer.id, "project_id": project.id,
                    "zone_id": zone.id, "location_id": location.id,
                    "availability_date": today, "responsible_id": self.env.user.id,
                    "notes": "Created/updated by the manual Server Demo Data scheduled action.",
                }
                if availability:
                    availability.write(availability_values)
                else:
                    availability = self.create(availability_values)
                availability.action_load_scope()

        # A clear daily replacement example, without changing permanent scope.
        first_location = created_locations.filtered(lambda item: item.code == "SRV-AIR-Z01-F01")[:1]
        if first_location:
            availability = self.search([
                ("location_id", "=", first_location.id), ("availability_date", "=", today)
            ], limit=1)
            helper_line = availability.line_ids.filtered(
                lambda line: line.resource_type_id == resource_types["HELPER"]
            )[:1]
            alternate = Resource.search([("code", "=", "SRV-ALT-HELPER-001")], limit=1)
            alternate_values = {
                "name": "Airtel Demo Alternate Helper", "code": "SRV-ALT-HELPER-001",
                "resource_type_id": resource_types["HELPER"].id, "ownership": "gsus_own",
                "customer_id": customer.id, "project_id": project.id,
                "zone_id": first_location.zone_id.id, "location_id": first_location.id,
                "shift_id": day_shift.id, "employment_type": "temporary", "status": "available",
                "remarks": "Server demo alternate resource",
            }
            if alternate:
                alternate.write(alternate_values)
            else:
                alternate = Resource.create(alternate_values)
            helper_line.write({
                "alternate_resource_ids": [(6, 0, alternate.ids)], "alternate_count": 1,
                "remarks": "One Helper unavailable; Airtel Demo Alternate Helper allocated",
            })
        return True

    @api.model
    def action_sync_jio_demo_data(self):
        """Create/update a separate, idempotent Jio demo dataset."""
        Partner = self.env["res.partner"].sudo()
        Project = self.env["project.project"].sudo()
        Zone = self.env["gsus.zone"].sudo()
        Location = self.env["gsus.location"].sudo()
        Requirement = self.env["gsus.requirement"].sudo()
        Resource = self.env["gsus.resource"].sudo()
        Vehicle = self.env["fleet.vehicle"].sudo()
        ResourceType = self.env["gsus.resource.type"].sudo()

        customer = Partner.search([
            ("is_telecom_customer", "=", True), ("name", "=", "Reliance Jio Demo Services")
        ], limit=1)
        values = {
            "name": "Reliance Jio Demo Services", "is_company": True,
            "is_telecom_customer": True, "phone": "+91 44 6666 2026",
            "email": "server.demo@jio.example",
        }
        if customer:
            customer.write(values)
        else:
            customer = Partner.create(values)

        project = Project.search([
            ("customer_id", "=", customer.id), ("name", "=", "Jio Tamil Nadu FRT Demo 2026")
        ], limit=1)
        values = {
            "name": "Jio Tamil Nadu FRT Demo 2026", "partner_id": customer.id,
            "customer_id": customer.id, "telecom_project_type": "frt_maintenance",
            "telecom_active": True,
        }
        if project:
            project.write(values)
        else:
            project = Project.create(values)

        types = {}
        for type_name, code, category in [
            ("Vehicle", "VEHICLE", "vehicle"), ("Driver", "DRIVER", "person"),
            ("Helper", "HELPER", "person"), ("Patroller", "PATROLLER", "person"),
        ]:
            resource_type = ResourceType.search([("code", "=", code)], limit=1)
            if not resource_type:
                resource_type = ResourceType.create({"name": type_name, "code": code, "category": category})
            types[code] = resource_type

        day_shift = self.env.ref("gsus_resource_allocation.shift_day_12")
        night_shift = self.env.ref("gsus_resource_allocation.shift_night_12")
        today = fields.Date.context_today(self)
        structure = {
            "Jio Demo Chennai Metro": ["Jio Adyar FRT", "Jio Guindy FRT"],
            "Jio Demo Tamil Nadu South": ["Jio Madurai FRT", "Jio Trichy FRT"],
        }
        scope = {"VEHICLE": 1, "DRIVER": 2, "HELPER": 4, "PATROLLER": 2}
        first_location = Location.browse()

        for zone_number, (zone_name, frt_names) in enumerate(structure.items(), 1):
            zone_code = "SRV-JIO-Z%02d" % zone_number
            zone = Zone.search([("code", "=", zone_code)], limit=1)
            zone_values = {"name": zone_name, "code": zone_code, "project_id": project.id, "active": True}
            if zone:
                zone.write(zone_values)
            else:
                zone = Zone.create(zone_values)

            for frt_number, frt_name in enumerate(frt_names, 1):
                frt_code = "%s-F%02d" % (zone_code, frt_number)
                location = Location.search([("code", "=", frt_code)], limit=1)
                location_values = {
                    "name": frt_name, "code": frt_code, "zone_id": zone.id,
                    "responsible_id": self.env.user.id, "active": True,
                }
                if location:
                    location.write(location_values)
                else:
                    location = Location.create(location_values)
                if not first_location:
                    first_location = location

                for type_code, required in scope.items():
                    resource_type = types[type_code]
                    requirement = Requirement.search([
                        ("location_id", "=", location.id), ("resource_type_id", "=", resource_type.id),
                    ], limit=1)
                    requirement_values = {
                        "location_id": location.id, "resource_type_id": resource_type.id,
                        "required_count": required, "notes": "Jio scheduled-action demo scope", "active": True,
                    }
                    if requirement:
                        requirement.write(requirement_values)
                    else:
                        requirement = Requirement.create(requirement_values)

                    persons = Resource.browse()
                    vehicles = Vehicle.browse()
                    if type_code == "VEHICLE":
                        plate = "TN-JIO-%02d%02d" % (zone_number, frt_number)
                        vehicle = Vehicle.search([("license_plate", "=", plate)], limit=1)
                        vehicle_values = {
                            "license_plate": plate, "gsus_customer_id": customer.id,
                            "gsus_project_id": project.id, "gsus_zone_id": zone.id,
                            "gsus_location_id": location.id, "gsus_status": "available",
                            "insurance_policy_no": "JIO-POL-%02d%02d" % (zone_number, frt_number),
                            "insurance_company": "Jio Demo Insurance",
                            "insurance_expiry_date": "2027-12-31", "permit_expiry_date": "2027-12-31",
                        }
                        if vehicle:
                            vehicle.write(vehicle_values)
                        else:
                            vehicle = Vehicle.create(vehicle_values)
                        vehicles = vehicle
                    else:
                        available = required - 1 if type_code == "HELPER" else required
                        for number in range(1, available + 1):
                            code = "JIO-%s-%02d%02d-%02d" % (type_code, zone_number, frt_number, number)
                            person = Resource.search([("code", "=", code)], limit=1)
                            person_values = {
                                "name": "%s %s %02d" % (frt_name, resource_type.name, number),
                                "code": code, "resource_type_id": resource_type.id,
                                "ownership": "gsus_own", "customer_id": customer.id,
                                "project_id": project.id, "zone_id": zone.id, "location_id": location.id,
                                "shift_id": day_shift.id if number % 2 else night_shift.id,
                                "status": "available", "remarks": "Jio scheduled-action demo person",
                            }
                            if person:
                                person.write(person_values)
                            else:
                                person = Resource.create(person_values)
                            persons |= person
                    requirement.write({
                        "assigned_resource_ids": [(6, 0, persons.ids)],
                        "assigned_vehicle_ids": [(6, 0, vehicles.ids)],
                    })

                availability = self.search([
                    ("location_id", "=", location.id), ("availability_date", "=", today)
                ], limit=1)
                availability_values = {
                    "customer_id": customer.id, "project_id": project.id, "zone_id": zone.id,
                    "location_id": location.id, "availability_date": today,
                    "responsible_id": self.env.user.id,
                    "notes": "Created/updated by the manual Jio Demo Data scheduled action.",
                }
                if availability:
                    availability.write(availability_values)
                else:
                    availability = self.create(availability_values)
                availability.action_load_scope()

        if first_location:
            availability = self.search([
                ("location_id", "=", first_location.id), ("availability_date", "=", today)
            ], limit=1)
            helper_line = availability.line_ids.filtered(
                lambda line: line.resource_type_id == types["HELPER"]
            )[:1]
            alternate = Resource.search([("code", "=", "JIO-ALT-HELPER-001")], limit=1)
            alternate_values = {
                "name": "Jio Demo Alternate Helper", "code": "JIO-ALT-HELPER-001",
                "resource_type_id": types["HELPER"].id, "ownership": "gsus_own",
                "customer_id": customer.id, "project_id": project.id,
                "zone_id": first_location.zone_id.id, "location_id": first_location.id,
                "shift_id": day_shift.id, "employment_type": "temporary",
                "status": "available", "remarks": "Jio demo alternate resource",
            }
            if alternate:
                alternate.write(alternate_values)
            else:
                alternate = Resource.create(alternate_values)
            helper_line.write({
                "alternate_resource_ids": [(6, 0, alternate.ids)], "alternate_count": 1,
                "remarks": "One Helper unavailable; Jio Demo Alternate Helper allocated",
            })
        return True
