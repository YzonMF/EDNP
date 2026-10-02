"""Seeds the database with the same demo data the app used to keep in JS.

Run with: python manage.py seed_demo_data
Safe to re-run — uses get_or_create / update_or_create throughout.
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from clergy.models import ClergyProfile
from registrar.models import ClergyRecord, Deanery, Parish

DEANERIES = {'Central Deanery': ['St. Barnabas - Alab', 'St. Thomas - Balili', 'St. George - Bilig', 'All Saints Cathedral - Bontoc', 'Holy Trinity - Dalikan', "St. Timothy's - Dantay", "St. Augustine's - Gonogon", "St. Michael's - Guina-ang", 'Annunciation - Maggon', "St. Joseph's - Mainit", "St. Gabriel's - Maligcong", 'Favuyan Mission - Maligcong', "St. Peter's - Sabangan", 'St. Paul - Samoki', 'St. Matthias - Tambingan', 'St. Agnes - Payag-eo', 'Mission - Barlig', 'Shalom - Capinitan', 'Mission - Talubin'], 'Titus Deanery': ["St. Theodore's - Basao", 'St. Andrews - Bugnay', "St. Paul's - Belwang", 'St. Bernard - Buscalan', 'St. James - Loccong', 'Holy Cross - Tocucan', "Dominic's - Tulgao", "St. Andrew's - Bugnay", "St. Luke's - Butbut", 'Transfiguration church - Maswa', 'St. Philip - Ngibat', 'Holy Trinity - Tinglayan'], 'Besao Deanery': ['St. Augustine - Agawa', 'St. Hippolytus - Ambagiw', 'St. Marks - Banguitan', 'St. Anne - Besao', 'St. Philip - Gueday', 'St. Benidict - Kin-iway', 'St. John the Divine - Lacmaan', 'St. Basil - Masameyeo', 'St. Caroline - Padangaan', 'St. Clement - Payeo', 'St. Ambrose - Suquib', "St. Luke's - Pangweo", 'St. Dunstan - Catenga', 'St. Jerome - Bunga'], 'Bauko Deanery': ['Holy Apostle - Abatan', 'St. Gregory - Bagnen', 'St. Andrew - Bagnen Oriente', 'Saint Martin - Balintaugan', 'Holy Innocent - Bebe', 'St. John - Bila', 'St. Gabriel - Data', 'San Luis - Mabaay', 'San Jose - Madepdepas', 'St. Matthias - Mayag', 'St. Mary - Mount Data', 'St. Paul - Otucan', 'St. Leo - Sadsadan', 'St. Francis - Cagubatan', 'St. Anselm - Pandayan', 'St. Bernard - Pangao', 'St. Mark - Sinto', 'Mission - Sengyew', 'San Pedro - Pasbol', 'St. John - Pasnadan', 'St. Gregory - Soysoyoc', 'St. Martin - Gotang'], 'Kinali Deanery': ['St Cyril - Bab-asig', 'St. Jerome - Bunga', 'St. Dunstan - Catengan', 'St. Hilarys - Dandanac', 'St. Marks - Pananuman', 'St. Bede - Panabungen', 'St. Andrew - Patiacan', 'St. Cyprian - Mabalite', 'St. Alfred - Tamboan', 'St. Luke - Maliten', 'St. Joseph - Lamag', 'St. Mathias - Supo', 'St. Stephen - Legleg', 'St. Polycarp - Matibuey'], 'Sagada Deanery': ['Ignacia & Juan - Aguid', 'St. Columba - Ambasing', 'Annunciation - Antadao', 'St. John - Balugan', 'St. Matthew - Bangaan', 'St. Mary Magdalene - Fedelisan', 'St. Elizabeth - Guesang', 'St. John - Pide', 'St. Mark - Tanulong', 'St. Mary - Tetep-an', 'St. Mary the Virgin - Sagada', 'Corpus Christi - Soyu', 'St. Simon Peter - Demang', 'St. Stephen - Nacagang', 'St. Agnes - Pide', "St. aidan's - Ankileng"], 'Tadian Deanery': ['Holy Trinity - Bantey', 'Holy Innocent - Batayan', 'St. Martha - Cabunagan', 'St. Francis - New Lubon', 'St. Gabriel - Lubon', 'St. Joseph - Masla', 'St. John the Baptist - Ilang', 'Mission - Duagan', 'Epiphany - Sumadel', 'St. Michael & all angels - Tadian', 'St. Philip - Tue', 'St. Thomas - Balaoa', 'St. Ignatius - Kayan', 'St. Catherine - Cervantes', 'St. Mark - Nabitic', 'St. Stephen - Bunga', 'St. Cyprian - Mabalite'], 'Special Assignments': ['Diocesan Office', 'Overseas Mission', 'Retired Clergy', 'Former Parish']}

RECORDS = [{'id': 'CLG-001', 'name': 'Rev. Fr. Juan Dela Cruz', 'dob': '1982-05-14', 'baptism': '1982-06-20', 'confirmation': '1994-05-15', 'gender': 'Male', 'phone': '0917-123-4567', 'email': 'juan.delacruz@example.org', 'address': 'Dagupan City, Pangasinan', 'ordination': '2014-06-20', 'assignment': 'St. Peter Parish', 'status': 'Active', 'contract': '2027-03-31'}, {'id': 'CLG-002', 'name': 'Rev. Fr. Pedro Santos', 'dob': '1978-09-21', 'baptism': '1978-10-01', 'confirmation': '1990-04-22', 'gender': 'Male', 'phone': '0918-222-3344', 'email': 'pedro.santos@example.org', 'address': 'Dagupan City, Pangasinan', 'ordination': '2010-05-18', 'assignment': 'Diocesan Office', 'status': 'Active', 'contract': '2027-01-15'}, {'id': 'CLG-003', 'name': 'Rev. Fr. Marco Reyes', 'dob': '1969-02-10', 'baptism': '1969-03-02', 'confirmation': '1981-05-10', 'gender': 'Male', 'phone': '0919-333-4455', 'email': 'marco.reyes@example.org', 'address': 'San Carlos City, Pangasinan', 'ordination': '1999-04-12', 'assignment': 'St. Joseph Parish', 'status': 'Active', 'contract': '2026-09-10'}, {'id': 'CLG-004', 'name': 'Rev. Fr. Antonio Garcia', 'dob': '1960-11-02', 'baptism': '1960-12-04', 'confirmation': '1972-05-21', 'gender': 'Male', 'phone': '0920-444-5566', 'email': 'antonio.garcia@example.org', 'address': 'Urdaneta City, Pangasinan', 'ordination': '1988-07-01', 'assignment': 'Rome Mission', 'status': 'Missionary to Other Diocese', 'contract': '2026-12-20'}, {'id': 'CLG-005', 'name': 'Rev. Fr. Luis Mendoza', 'dob': '1957-03-16', 'baptism': '1957-04-07', 'confirmation': '1969-05-18', 'gender': 'Male', 'phone': '0921-555-6677', 'email': 'luis.mendoza@example.org', 'address': 'Pozorrubio, Pangasinan', 'ordination': '1984-05-11', 'assignment': 'Retired Clergy', 'status': 'Retired', 'contract': None, 'retirement': '2019-12-31'}, {'id': 'CLG-006', 'name': 'Rev. Fr. Ramon Cruz', 'dob': '1985-07-08', 'baptism': '1985-08-04', 'confirmation': '1997-05-25', 'gender': 'Male', 'phone': '0922-666-7788', 'email': 'ramon.cruz@example.org', 'address': 'Calasiao, Pangasinan', 'ordination': '2016-03-25', 'assignment': 'Former Parish', 'status': 'On Leave', 'leave_reason': 'Medical leave', 'contract': None}]


class Command(BaseCommand):
    help = "Seed deaneries/parishes, demo clergy records, and demo login accounts."

    def handle(self, *args, **options):
        # Deaneries + parishes
        for deanery_name, entries in DEANERIES.items():
            deanery, _ = Deanery.objects.get_or_create(name=deanery_name)
            for entry in entries:
                if ' - ' in entry:
                    parish_name, place_name = entry.split(' - ', 1)
                else:
                    parish_name, place_name = entry, None
                Parish.objects.get_or_create(deanery=deanery, parish_name=parish_name, place_name=place_name)
        self.stdout.write(self.style.SUCCESS(f"Deaneries/parishes seeded ({Deanery.objects.count()} deaneries, {Parish.objects.count()} parishes)."))

        # Clergy records
        for r in RECORDS:
            ClergyRecord.objects.update_or_create(
                id=r['id'],
                defaults={
                    'name': r['name'],
                    'gender': r.get('gender'),
                    'date_of_birth': r.get('dob'),
                    'baptism_date': r.get('baptism'),
                    'confirmation_date': r.get('confirmation'),
                    'ordination_date': r.get('ordination'),
                    'phone': r.get('phone'),
                    'email': r.get('email'),
                    'address': r.get('address'),
                    'assignment': r.get('assignment'),
                    'status': r['status'],
                    'leave_reason': r.get('leave_reason'),
                    'contract_end': r.get('contract'),
                    'retirement_date': r.get('retirement'),
                },
            )
        self.stdout.write(self.style.SUCCESS(f"Clergy records seeded ({ClergyRecord.objects.count()})."))

        # Registrar demo login
        registrar_user, created = User.objects.get_or_create(
            username='registrar',
            defaults={'is_staff': True, 'first_name': 'Registrar'},
        )
        registrar_user.is_staff = True
        registrar_user.set_password('password')
        registrar_user.save()
        self.stdout.write(self.style.SUCCESS("Registrar demo login ready: registrar / password"))

        # Clergy demo login, linked to CLG-001 (see note in database/ednp_schema.sql
        # about the two original prototypes having mismatched demo IDs).
        clergy_user, created = User.objects.get_or_create(username='clergy')
        clergy_user.is_staff = False
        clergy_user.set_password('password')
        clergy_user.save()

        record = ClergyRecord.objects.get(id='CLG-001')
        ClergyProfile.objects.update_or_create(
            user=clergy_user,
            defaults={
                'record': record,
                'first_name': 'Juan',
                'last_name': 'Dela Cruz',
                'birth_date': '1985-05-15',
                'place_of_birth': 'Baguio City',
                'gender': 'Male',
                'civil_status': 'Single',
                'baptism_date': '1985-06-01',
                'baptism_place': 'St. Peter Parish',
                'confirmation_date': '1990-07-15',
                'confirmation_place': 'St. Peter Parish',
                'address': 'Baguio City, Philippines',
                'contact': '09123456789',
                'email': 'juan@example.com',
                'ordination_date': '2014-04-01',
                'ordination_place': 'Baguio Cathedral',
                'assignment': 'St. Peter Parish',
                'previous_assignment': 'St. Paul Parish',
                'education': 'Seminary and theological studies',
                'status': 'Active',
                'diocese': 'Episcopal Diocese of Northern Philippines',
                'contract_end': '2027-03-31',
                'verification': 'Verified',
            },
        )
        self.stdout.write(self.style.SUCCESS("Clergy demo login ready: clergy / password (linked to CLG-001)"))
