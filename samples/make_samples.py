"""Generates sample upload files (fictional people) in this folder.
Run:  python make_samples.py     (needs openpyxl + reportlab)"""
from pathlib import Path

import openpyxl
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).parent
HEAD = ["Name", "Employee Number", "Roles", "Skills", "Skill Ratings", "Background"]
PEOPLE = [
    ("Ana Reyes", "E1001", "Backend Developer", "Python (4), Postgres (4), FastAPI (3), Docker (3)", "Six years building REST services in fintech."),
    ("Ben Cruz", "E1002", "Frontend Developer", "ReactJS (5), TypeScript (4), HTML & CSS (4), Next JS (3)", "Led a design-system migration."),
    ("Carla Santos", "E1003", "Data Scientist", "py (4), Machine learning (4), SQL (3), PyTorch (3), NLP (2)", "MSc in statistics, applied ML in retail."),
    ("Dan Lim", "E1004", "DevOps Engineer", "k8s (4), Docker (5), Terraform (3), AWS (4), CI/CD (4)", "Runs platform for 40 microservices."),
    ("Ella Tan", "E1005", "QA Engineer", "Selenium (4), Manual Testing (5), Agile (3), SQL (2)", "Test lead on two regulated products."),
    ("Farah Ali", "E1006", "Project Manager", "Project mgmt (5), Scrum (4), Stakeholder Management (4), Risk Management (3), Excel (4)", "PMP-certified, 10 years delivering IT projects."),
    ("Gio Ramos", "E1007", "Full Stack Developer", "JS (4), Node (4), React (3), MongoDB (3), GraphQL (2), Kubernets (1)", "Startup background, ships fast."),
    ("Hana Park", "E1008", "HR Manager", "Recruitment (5), Performance Management (4), Learning & Development (4), Communication (5)", "Runs L&D programmes for 300 staff."),
]


def wide_xlsx():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Skills"
    ws["A1"] = "Company skills inventory (exported)"  # title row above the header: tests header detection
    ws.append([])
    ws.append(HEAD)
    for name, no, role, skills, bg in PEOPLE:
        inline = skills
        ws.append([name, no, role, inline, None, bg])
    # parallel-list style row: skills and ratings in separate cells
    ws.append(["Ivy Co", "E1009", "Business Analyst; Scrum Master", "Business Analysis, Agile, SQL, Power BI", "4, 3, 3, 4", "Ex-consultant"])
    wb.save(OUT / "employees_wide.xlsx")


def long_xlsx():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Employee Name", "Employee No.", "Job Title", "Skill", "Rating", "Summary"])
    ws.append(["Jun Diaz", "E2001", "Backend Developer", "Python", 4, "API developer"])
    ws.append([None, None, None, "SQL", 3, None])  # merged-cell style continuation rows
    ws.append([None, None, None, "Docker", 2, None])
    ws.append(["Kai Uy", "E2002", "Data Engineer", "Apache Spark", 4, "Pipelines"])
    ws.append([None, None, None, "ETL", 5, None])
    wb.save(OUT / "employees_long.xlsx")


def table_pdf():
    doc = SimpleDocTemplate(str(OUT / "employees_table.pdf"), pagesize=landscape(A4))
    st = getSampleStyleSheet()["BodyText"]
    data = [HEAD] + [[Paragraph(str(c), st) for c in (n, no, r, s, "", b)] for n, no, r, s, b in PEOPLE[:5]]
    t = Table(data, repeatRows=1, colWidths=[80, 110, 100, 200, 70, 170])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
    doc.build([t])


def text_pdf():
    doc = SimpleDocTemplate(str(OUT / "employees_text.pdf"), pagesize=A4)
    st = getSampleStyleSheet()["BodyText"]
    story = []
    for n, no, r, s, bg in PEOPLE[5:8]:
        for line in (f"Name: {n}", f"Employee Number: {no}", f"Roles: {r}", f"Skills: {s}", f"Background: {bg}"):
            story.append(Paragraph(line, st))
        story.append(Spacer(1, 14))
    doc.build(story)


if __name__ == "__main__":
    wide_xlsx(); long_xlsx(); table_pdf(); text_pdf()
    print("Wrote sample files to", OUT)
