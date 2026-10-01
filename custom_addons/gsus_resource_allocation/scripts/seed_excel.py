"""One-time idempotent seed for the customer workbook supplied on 24 Aug 2026.

Run through ``odoo-bin shell`` where ``env`` is provided by Odoo.
"""
from datetime import date, timedelta
from odoo import Command

Location = env["gsus.location"]
Type = env["gsus.resource.type"]
Req = env["gsus.requirement"]
Av = env["gsus.availability"]
Gap = env["gsus.gap"]
Worker = env["gsus.worker"]
Assignment = env["gsus.assignment"]
types = {record.code: record for record in Type.search([])}

# Sheet 1: FRT -> vehicle, helper, patroller, driver; each value is (scope, available).
rows = {
    "Velachery": ((1, 1), (4, 4), (2, 2), (2, 2)), "T Nagar": ((1, 1), (4, 4), (3, 3), (2, 2)),
    "Purasaiwakkam": ((1, 1), (4, 2), (2, 2), (2, 2)), "Siruseri": ((1, 1), (4, 4), (3, 3), (2, 2)),
    "Tambaram": ((1, 1), (4, 1), (4, 3), (2, 2)), "Ambattur": ((1, 1), (4, 2), (4, 3), (2, 1)),
    "Porur": ((1, 1), (4, 3), (3, 2), (2, 2)), "Guindy": ((1, 1), (4, 2), (1, 1), (2, 2)),
    "Oragadam": ((1, 1), (2, 2), (4, 4), (1, 1)), "Salem": ((1, 1), (2, 2), (3, 3), (1, 1)),
    "Krishnagiri": ((1, 1), (2, 2), (4, 4), (1, 1)), "Thiruvannamalai": ((1, 1), (2, 2), (4, 4), (1, 1)),
    "Vellore": ((1, 1), (2, 2), (3, 3), (1, 1)), "Kanchipuram": ((1, 1), (2, 2), (4, 4), (1, 1)),
    "Namakkal": ((1, 1), (2, 2), (5, 5), (1, 1)), "Villiyanur": ((1, 1), (2, 2), (4, 3), (1, 1)),
    "Tindivanam": ((1, 1), (2, 2), (4, 4), (1, 1)), "Virudhachalam": ((1, 1), (2, 2), (4, 4), (1, 1)),
    "Madurai": ((0, 0), (0, 0), (4, 4), (0, 0)),
}
codes = ["VEHICLE", "HELPER", "PATROLLER", "DRIVER"]
for location_name, values in rows.items():
    location = Location.search([("name", "=", location_name)], limit=1)
    for code, (required, available) in zip(codes, values):
        requirement = Req.search([("location_id", "=", location.id), ("resource_type_id", "=", types[code].id)], limit=1)
        if requirement:
            requirement.required_count = required
        else:
            Req.create({"location_id": location.id, "resource_type_id": types[code].id, "required_count": required})
    if not Av.search([("availability_date", "=", date(2026, 8, 24)), ("shift", "=", "day"), ("location_id", "=", location.id)], limit=1):
        lines = []
        for code, (required, available) in zip(codes, values):
            remarks = ""
            if location_name in ("Velachery", "Purasaiwakkam") and code == "VEHICLE": remarks = "Insurance expired"
            if location_name in ("Velachery", "T Nagar", "Ambattur") and code == "DRIVER": remarks = "24-hour continuation noted in source Excel"
            lines.append(Command.create({"resource_type_id": types[code].id, "required_count": required, "available_count": available, "remarks": remarks}))
        Av.create({"availability_date": date(2026, 8, 24), "shift": "day", "location_id": location.id, "line_ids": lines, "state": "approved", "notes": "Imported and normalized from the supplied 24 Aug 2026 workbook."})

for location_name, code in [("Porur", "SPLICER"), ("Porur", "ENGINEER"), ("T Nagar", "ENGINEER"), ("Guindy", "ENGINEER"), ("Tambaram", "SPLICER")]:
    location = Location.search([("name", "=", location_name)], limit=1)
    if not Req.search([("location_id", "=", location.id), ("resource_type_id", "=", types[code].id)], limit=1):
        Req.create({"location_id": location.id, "resource_type_id": types[code].id, "required_count": 1, "notes": "Scope inferred from Sheet 2; verify with operations."})

for worker_name, location_name in [("Arun", "Porur"), ("Vijayan", "Tambaram"), ("Venkatesh Babu", "Ambattur")]:
    worker = Worker.search([("name", "=", worker_name), ("resource_type_id", "=", types["PATROLLER"].id)], limit=1)
    if not worker:
        worker = Worker.create({"name": worker_name, "resource_type_id": types["PATROLLER"].id, "employment_type": "permanent", "notes": "Imported from Sheet 2; complete employee details."})
    if worker_name == "Arun" and not Assignment.search([("worker_id", "=", worker.id), ("state", "=", "active")], limit=1):
        Assignment.create({"worker_id": worker.id, "location_id": Location.search([("name", "=", location_name)], limit=1).id, "start_date": date(2026, 1, 1), "shift": "both", "state": "active", "notes": "Named FRT assignment imported from the source register."})

gaps = [
    ("T Nagar", "ENGINEER", 1, 188, None, "Engineers not available"), ("Guindy", "ENGINEER", 1, 110, None, "Engineers not available"),
    ("Porur", "ENGINEER", 1, 86, None, "Engineers not available"), ("Porur", "PATROLLER", 1, 30, "Arun", "Patroller not available"),
    ("Tambaram", "PATROLLER", 1, None, "Vijayan", "Patroller gap"), ("Ambattur", "PATROLLER", 1, 15, "Venkatesh Babu", "Patroller not available"),
    ("Villiyanur", "PATROLLER", 1, None, None, "Patroller gap"), ("Velachery", "HELPER", 2, None, None, "Helper gap"),
    ("T Nagar", "HELPER", 2, None, None, "Helper gap"), ("Tambaram", "HELPER", 2, None, None, "Helper gap"),
    ("Porur", "HELPER", 2, None, None, "Helper gap"), ("Porur", "SPLICER", 1, None, None, "Splicer gap"),
    ("Porur", "DRIVER", 1, None, None, "Driver gap"), ("Porur", "VEHICLE", 1, None, None, "Vehicle gap"),
    ("Ambattur", "HELPER", 1, None, None, "Helper gap"), ("Guindy", "DRIVER", 1, None, None, "Driver gap"),
    ("Oragadam", "VEHICLE", 1, None, None, "Vehicle gap"), ("Tambaram", "SPLICER", 1, None, None, "Splicer gap"),
    ("Purasaiwakkam", "DRIVER", 1, None, None, "Driver gap"), ("Velachery", "DRIVER", 1, None, None, "Driver gap"),
    ("Tambaram", "DRIVER", 1, None, None, "Driver gap"),
]
snapshot_date = date(2026, 8, 24)
for location_name, code, count, aging, worker_name, reason in gaps:
    location = Location.search([("name", "=", location_name)], limit=1)
    if not Gap.search([("location_id", "=", location.id), ("resource_type_id", "=", types[code].id), ("reason", "=", reason)], limit=1):
        worker = Worker.search([("name", "=", worker_name)], limit=1) if worker_name else Worker
        Gap.create({"location_id": location.id, "resource_type_id": types[code].id, "gap_count": count, "gap_start_date": snapshot_date - timedelta(days=aging or 0), "worker_id": worker.id if worker else False, "reason": reason})

admin = env.ref("base.user_admin")
admin.password = "GSUS@2026"
portal_group = env.ref("base.group_portal")
portal = env["res.users"].search([("login", "=", "porur.portal@gsus.local")], limit=1)
if not portal:
    partner = env["res.partner"].create({"name": "Porur FRT Portal", "email": "porur.portal@gsus.local"})
    portal = env["res.users"].with_context(no_reset_password=True).create({"name": "Porur FRT Portal", "login": "porur.portal@gsus.local", "password": "Porur@2026", "partner_id": partner.id, "group_ids": [Command.set([portal_group.id])]})
Location.search([("name", "=", "Porur")], limit=1).write({"portal_user_ids": [Command.link(portal.id)]})
env.cr.commit()
print("zones", env["gsus.zone"].search_count([]), "locations", Location.search_count([]), "requirements", Req.search_count([]), "snapshots", Av.search_count([]), "gaps", Gap.search_count([]), "workers", Worker.search_count([]))
