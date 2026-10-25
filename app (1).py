"""Student Management System (per flowchart: all key features + AI tools) - Python + SQLite, zero installs.
Run: python app.py -> http://localhost:8000
Admin: admin@uni.edu/admin123 | Faculty: f1@uni.edu/faculty123 | Student: s1@uni.edu/student123
"""
import sqlite3, json, hashlib, secrets, random, re, datetime, base64, os, mimetypes, math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = "sms3.db"; SESSIONS = {}
H = lambda p: hashlib.sha256(p.encode()).hexdigest()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','faculty','student')), status TEXT DEFAULT 'active' CHECK(status IN ('active','blocked')), created_at TEXT DEFAULT CURRENT_TIMESTAMP, last_login TEXT);
CREATE TABLE IF NOT EXISTS admins(admin_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE REFERENCES users(user_id) ON DELETE CASCADE, designation TEXT);
CREATE TABLE IF NOT EXISTS departments(department_id INTEGER PRIMARY KEY AUTOINCREMENT, department_name TEXT UNIQUE NOT NULL, department_code TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS courses(course_id INTEGER PRIMARY KEY AUTOINCREMENT, course_name TEXT NOT NULL, course_code TEXT UNIQUE NOT NULL, department_id INTEGER REFERENCES departments(department_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS subjects(subject_id INTEGER PRIMARY KEY AUTOINCREMENT, subject_name TEXT NOT NULL, subject_code TEXT UNIQUE NOT NULL, course_id INTEGER REFERENCES courses(course_id) ON DELETE CASCADE, semester INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS faculty(faculty_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE REFERENCES users(user_id) ON DELETE CASCADE, employee_code TEXT UNIQUE NOT NULL, department_id INTEGER REFERENCES departments(department_id),
  designation TEXT, qualification TEXT, experience_years INTEGER DEFAULT 0, specialization TEXT,
  joining_date TEXT DEFAULT CURRENT_DATE, status TEXT DEFAULT 'active');
CREATE TABLE IF NOT EXISTS students(student_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE REFERENCES users(user_id) ON DELETE CASCADE, enrollment_number TEXT UNIQUE NOT NULL,
  course_id INTEGER REFERENCES courses(course_id), semester INTEGER DEFAULT 1, section TEXT DEFAULT 'A', study_hours REAL DEFAULT 2,
  prev_sem_percent REAL DEFAULT 60, backlogs INTEGER DEFAULT 0 CHECK(backlogs>=0), participation REAL DEFAULT 5 CHECK(participation BETWEEN 0 AND 10),
  admission_date TEXT DEFAULT CURRENT_DATE, status TEXT DEFAULT 'active');
CREATE TABLE IF NOT EXISTS faculty_subjects(faculty_id INTEGER REFERENCES faculty(faculty_id) ON DELETE CASCADE, subject_id INTEGER REFERENCES subjects(subject_id) ON DELETE CASCADE, section TEXT DEFAULT 'A', PRIMARY KEY(faculty_id,subject_id,section));
CREATE TABLE IF NOT EXISTS timetable(timetable_id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER REFERENCES subjects(subject_id) ON DELETE CASCADE, faculty_id INTEGER REFERENCES faculty(faculty_id) ON DELETE CASCADE,
  section TEXT, day TEXT CHECK(day IN ('Mon','Tue','Wed','Thu','Fri','Sat')), start_time TEXT, end_time TEXT);
CREATE TABLE IF NOT EXISTS assignments(assignment_id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER REFERENCES subjects(subject_id) ON DELETE CASCADE, faculty_id INTEGER REFERENCES faculty(faculty_id), title TEXT NOT NULL, description TEXT, deadline TEXT);
CREATE TABLE IF NOT EXISTS submissions(submission_id INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id INTEGER REFERENCES assignments(assignment_id) ON DELETE CASCADE, student_id INTEGER REFERENCES students(student_id) ON DELETE CASCADE,
  submitted_at TEXT DEFAULT CURRENT_TIMESTAMP, file_url TEXT, marks REAL CHECK(marks BETWEEN 0 AND 10), UNIQUE(assignment_id,student_id));
CREATE TABLE IF NOT EXISTS attendance(attendance_id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER REFERENCES students(student_id) ON DELETE CASCADE, subject_id INTEGER REFERENCES subjects(subject_id) ON DELETE CASCADE,
  faculty_id INTEGER REFERENCES faculty(faculty_id), date TEXT NOT NULL, status TEXT CHECK(status IN ('P','A')), UNIQUE(student_id,subject_id,date));
CREATE TABLE IF NOT EXISTS results(result_id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER REFERENCES students(student_id) ON DELETE CASCADE, subject_id INTEGER REFERENCES subjects(subject_id) ON DELETE CASCADE,
  exam_type TEXT NOT NULL, marks REAL, max_marks REAL NOT NULL, CHECK(marks>=0 AND marks<=max_marks), UNIQUE(student_id,subject_id,exam_type));
CREATE TABLE IF NOT EXISTS materials(material_id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER REFERENCES subjects(subject_id) ON DELETE CASCADE, faculty_id INTEGER, title TEXT, url TEXT);
CREATE TABLE IF NOT EXISTS announcements(announcement_id INTEGER PRIMARY KEY AUTOINCREMENT, created_by INTEGER REFERENCES users(user_id), title TEXT NOT NULL, message TEXT,
  audience TEXT DEFAULT 'all' CHECK(audience IN ('all','students','faculty')), created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS notifications(notification_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE, message TEXT, is_read INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS audit_logs(log_id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, ts TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS leave_requests(leave_id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER REFERENCES students(student_id) ON DELETE CASCADE,
  from_date TEXT, to_date TEXT, reason TEXT, status TEXT DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')), created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS fees(fee_id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER REFERENCES students(student_id) ON DELETE CASCADE, description TEXT, amount REAL CHECK(amount>0),
  due_date TEXT, status TEXT DEFAULT 'pending' CHECK(status IN ('pending','paid')), payment_date TEXT);
CREATE TABLE IF NOT EXISTS exams(exam_id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER REFERENCES subjects(subject_id) ON DELETE CASCADE, exam_type TEXT, exam_date TEXT, max_marks REAL);
CREATE TABLE IF NOT EXISTS history(attendance REAL, study_hours REAL, assign REAL, current_marks REAL, last_exam REAL, prev_sem REAL, backlogs REAL, participation REAL, final_percent REAL);
CREATE VIEW IF NOT EXISTS v_results AS SELECT r.*, sb.subject_name, sb.subject_code, ROUND(100.0*r.marks/r.max_marks,1) AS pct FROM results r JOIN subjects sb ON sb.subject_id=r.subject_id;
CREATE VIEW IF NOT EXISTS v_attendance AS SELECT student_id, subject_id, COUNT(*) total, SUM(status='P') present, ROUND(100.0*SUM(status='P')/COUNT(*),1) pct FROM attendance GROUP BY student_id, subject_id;
"""
TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS trg_res_ins AFTER INSERT ON results BEGIN
  INSERT INTO audit_logs(action) VALUES('Result added: student '||NEW.student_id||', subject '||NEW.subject_id||', '||NEW.exam_type||' = '||NEW.marks);
  INSERT INTO notifications(user_id,message) SELECT user_id,'New result: '||NEW.exam_type||' '||NEW.marks||'/'||NEW.max_marks FROM students WHERE student_id=NEW.student_id; END;
CREATE TRIGGER IF NOT EXISTS trg_res_upd AFTER UPDATE OF marks ON results BEGIN
  INSERT INTO audit_logs(action) VALUES('Result updated: student '||NEW.student_id||', subject '||NEW.subject_id||', '||NEW.exam_type||': '||OLD.marks||' -> '||NEW.marks); END;
CREATE TRIGGER IF NOT EXISTS trg_user_del AFTER DELETE ON users BEGIN INSERT INTO audit_logs(action) VALUES('User deleted: '||OLD.email||' ('||OLD.role||')'); END;
CREATE TRIGGER IF NOT EXISTS trg_leave AFTER UPDATE OF status ON leave_requests BEGIN
  INSERT INTO notifications(user_id,message) SELECT user_id,'Your leave request was '||NEW.status FROM students WHERE student_id=NEW.student_id;
  INSERT INTO audit_logs(action) VALUES('Leave #'||NEW.leave_id||' '||NEW.status); END;
CREATE TRIGGER IF NOT EXISTS trg_fee_ins AFTER INSERT ON fees BEGIN
  INSERT INTO notifications(user_id,message) SELECT user_id,'New fee: '||NEW.description||' - Rs '||NEW.amount FROM students WHERE student_id=NEW.student_id; END;
CREATE TRIGGER IF NOT EXISTS trg_fee_paid AFTER UPDATE OF status ON fees WHEN NEW.status='paid' BEGIN
  INSERT INTO audit_logs(action) VALUES('Fee #'||NEW.fee_id||' paid by student '||NEW.student_id); END;
CREATE TRIGGER IF NOT EXISTS trg_ann AFTER INSERT ON announcements BEGIN
  INSERT INTO notifications(user_id,message) SELECT user_id,'Announcement: '||NEW.title FROM users WHERE NEW.audience='all' OR (NEW.audience='students' AND role='student') OR (NEW.audience='faculty' AND role='faculty'); END;
"""

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c
nz = lambda b, k, d: d if b.get(k) is None else b[k]
FAC_STUDENTS = ("SELECT DISTINCT x.student_id FROM faculty_subjects fs JOIN subjects sb ON sb.subject_id=fs.subject_id JOIN students x ON x.course_id=sb.course_id "
                "AND x.semester=sb.semester AND x.section=fs.section WHERE fs.faculty_id=?")
Q = lambda c, q, *a: [dict(r) for r in c.execute(q, a)]
def mku(c, n, e, pw, role): return c.execute("INSERT INTO users(full_name,email,password_hash,role) VALUES(?,?,?,?)", (n, e, H(pw), role)).lastrowid

def init():
    c = db(); c.executescript(SCHEMA)
    if not c.execute("SELECT 1 FROM users").fetchone():
        random.seed(3); c.execute("INSERT INTO admins(user_id,designation) VALUES(?, 'Registrar')", (mku(c, "System Admin", "admin@uni.edu", "admin123", "admin"),))
        c.execute("INSERT INTO departments(department_name,department_code) VALUES('Computer Science','CSE'),('Electronics','ECE')")
        c.execute("INSERT INTO courses(course_name,course_code,department_id) VALUES('B.Tech Computer Science','BTCS',1)")
        for i, n in enumerate(["Dr. Meera Iyer", "Prof. Kunal Joshi"]):
            c.execute("INSERT INTO faculty(user_id,employee_code,department_id,designation,qualification,experience_years,specialization) VALUES(?,?,1,?,?,?,?)", (mku(c, n, f"f{i+1}@uni.edu", "faculty123", "faculty"), f"EMP10{i+1}", *[("Professor", "Ph.D. (Computer Science)", 15, "Databases & Data Mining"), ("Assistant Professor", "M.Tech (CSE)", 6, "Operating Systems")][i]))
        subs = [("Database Management Systems", "CS301", 1), ("Operating Systems", "CS302", 2), ("Computer Networks", "CS303", 1), ("Software Engineering", "CS304", 2)]
        for n, code, f in subs:
            sid = c.execute("INSERT INTO subjects(subject_name,subject_code,course_id,semester) VALUES(?,?,1,5)", (n, code)).lastrowid
            c.execute("INSERT INTO faculty_subjects VALUES(?,?, 'A')", (f, sid))
            for d, t in [("Mon", "09:00"), ("Wed", "11:00"), ("Fri", "10:00")][:2 + sid % 2]:
                c.execute("INSERT INTO timetable(subject_id,faculty_id,section,day,start_time,end_time) VALUES(?,?, 'A',?,?,?)", (sid, f, d, t, f"{int(t[:2])+1}:00"))
            c.execute("INSERT INTO assignments(subject_id,faculty_id,title,description,deadline) VALUES(?,?,?,?,?)", (sid, f, f"{code} Assignment 1", "Solve the problem set and upload the link.", "2026-12-01"))
            c.execute("INSERT INTO materials(subject_id,faculty_id,title,url) VALUES(?,?,?,?)", (sid, f, f"{n} - Unit 1 Notes", "https://example.com/notes.pdf"))
        today = datetime.date.today()
        for i, (n, sh) in enumerate([("Aarav Shah", 3.5), ("Diya Patel", 2.5), ("Rohan Mehta", 1.2), ("Isha Verma", 4.2)]):
            st = c.execute("INSERT INTO students(user_id,enrollment_number,course_id,semester,section,study_hours,prev_sem_percent,backlogs,participation) VALUES(?,?,1,5,'A',?,?,?,?)", (mku(c, n, f"s{i+1}@uni.edu", "student123", "student"), f"EN2024{101+i}", sh, [78, 66, 52, 85][i], [0, 1, 3, 0][i], [8, 6, 4, 9][i])).lastrowid
            for sb in range(1, 5):
                for d in range(14):
                    c.execute("INSERT INTO attendance(student_id,subject_id,faculty_id,date,status) VALUES(?,?,?,?,?)", (st, sb, subs[sb-1][2], str(today - datetime.timedelta(days=d)), "P" if random.random() < min(.97, .45 + .13 * sh) else "A"))
                q = min(.95, (sh * 9 + random.randint(28, 45)) / 100)
                c.execute("INSERT INTO results(student_id,subject_id,exam_type,marks,max_marks) VALUES(?,?,'Internal',?,50),(?,?,'Midterm',?,50)", (st, sb, round(50 * q), st, sb, round(50 * min(.97, q + random.uniform(-.08, .08)))))
                c.execute("INSERT INTO submissions(assignment_id,student_id,file_url,marks) VALUES(?,?,?,?)", (sb, st, "https://example.com/work", round(min(10, q * 10 + random.uniform(-1, 1)), 1)))
        c.execute("INSERT INTO announcements(created_by,title,message,audience) VALUES(1,'Welcome','Semester 5 portal is live. Check timetable and assignments.','all')")
        for _ in range(250):
            a, s, asg, cm, le, pv, bl, pt = random.uniform(40, 100), random.uniform(.5, 6), random.uniform(30, 100), random.uniform(30, 95), random.uniform(30, 98), random.uniform(35, 95), random.randint(0, 4), random.uniform(2, 10)
            c.execute("INSERT INTO history VALUES(?,?,?,?,?,?,?,?,?)", (a, s, asg, cm, le, pv, bl, pt, max(0, min(100, .35 * le + .2 * cm + .12 * pv + .08 * a + 1.5 * s + .06 * asg + 1.2 * pt - 1.8 * bl + 4 + random.gauss(0, 2.5)))))
        for sb, dd in [(1, 9), (2, 12), (3, 15), (4, 18)]:
            c.execute("INSERT INTO exams(subject_id,exam_type,exam_date,max_marks) VALUES(?,?,?,?)", (sb, "Final", str(today + datetime.timedelta(days=dd)), 100))
        for k in range(1, 5):
            c.execute("INSERT INTO fees(student_id,description,amount,due_date,status) VALUES(?,?,?,?,?)", (k, "Semester 5 Tuition", 45000, "2026-11-30", "paid" if k == 1 else "pending"))
        c.execute("INSERT INTO leave_requests(student_id,from_date,to_date,reason) VALUES(2,?,?,'Family function')", (str(today + datetime.timedelta(days=3)), str(today + datetime.timedelta(days=4))))
    c.executescript(TRIGGERS); c.commit(); c.close()

_W = []
def weights():  # multiple linear regression via normal equations (trained once, cached)
    if _W: return _W[0]
    c = db(); rows = c.execute("SELECT * FROM history").fetchall(); c.close()
    X = [[1, *r[:-1]] for r in rows]; y = [r[-1] for r in rows]; n = len(X[0])
    A = [[sum(x[i] * x[j] for x in X) for j in range(n)] + [sum(x[i] * t for x, t in zip(X, y))] for i in range(n)]
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(A[r][i])); A[i], A[p] = A[p], A[i]; A[i] = [v / A[i][i] for v in A[i]]
        for r in range(n):
            if r != i: A[r] = [a - A[r][i] * b for a, b in zip(A[r], A[i])]
    _W.append([A[i][n] for i in range(n)]); return _W[0]

def predict(*f):
    w = weights(); return round(max(0, min(100, w[0] + sum(a * b for a, b in zip(w[1:], f)))), 1), [round(v, 3) for v in w]

def stats(c, sid):
    g = lambda q, *a: round(c.execute(q, a or (sid,)).fetchone()[0] or 0, 1)
    s = c.execute("SELECT study_hours, prev_sem_percent, backlogs, participation FROM students WHERE student_id=?", (sid,)).fetchone()
    d = dict(att=g("SELECT AVG(100.0*(status='P')) FROM attendance WHERE student_id=?"), marks=g("SELECT AVG(pct) FROM v_results WHERE student_id=?"),
             assign=g("SELECT AVG(marks)*10 FROM submissions WHERE student_id=? AND marks IS NOT NULL"),
             last=g("SELECT AVG(pct) FROM v_results WHERE student_id=? AND exam_type=(SELECT exam_type FROM v_results WHERE student_id=? ORDER BY result_id DESC LIMIT 1)", sid, sid),
             study=s[0], prev=s[1], backlogs=s[2], part=s[3])
    d["predicted"] = predict(d["att"], d["study"], d["assign"], d["marks"], d["last"], d["prev"], d["backlogs"], d["part"])[0]; return d

TOPICS = {"database": ["Normalization (1NF-BCNF)", "SQL joins & subqueries", "Transactions & ACID", "Indexing & query optimization"],
          "operating": ["Process scheduling", "Deadlocks", "Memory management & paging", "File systems"],
          "network": ["OSI / TCP-IP layers", "Routing algorithms", "TCP vs UDP", "Subnetting & IP addressing"],
          "software": ["SDLC models", "UML diagrams", "Software testing", "Agile & Scrum"]}
topics_for = lambda name: next((v for k, v in TOPICS.items() if k in name.lower()), ["Core concepts", "Previous-year questions", "Practice problems"])
KB = [(["normalization", "normal form", "1nf", "2nf", "3nf", "bcnf"], "<b>Normalization</b> organizes tables to remove redundancy and update anomalies: 1NF (atomic values), 2NF (no partial dependency), 3NF (no transitive dependency), BCNF (every determinant is a candidate key)."),
      (["acid", "transaction"], "<b>ACID</b> properties of a transaction: Atomicity (all or nothing), Consistency (valid state to valid state), Isolation (concurrent transactions don't interfere), Durability (committed data survives crashes)."),
      (["primary key"], "A <b>primary key</b> uniquely identifies each row. It cannot be NULL or duplicated. Example: student_id in the students table."),
      (["foreign key"], "A <b>foreign key</b> links a column to the primary key of another table and enforces referential integrity (e.g. results.student_id references students.student_id)."),
      (["join"], "<b>JOINs</b> combine rows of tables: INNER (matching rows only), LEFT (all left rows + matches), RIGHT, FULL. Example: SELECT u.full_name, r.marks FROM results r JOIN students s ON ... JOIN users u ON ..."),
      (["index", "indexing"], "An <b>index</b> (usually a B-tree) speeds up searches on a column at the cost of extra storage and slower writes. Index columns used in WHERE and JOIN."),
      (["trigger"], "A <b>trigger</b> is SQL that runs automatically on INSERT/UPDATE/DELETE. This project uses triggers to write audit logs and send notifications."),
      (["view"], "A <b>view</b> is a saved SELECT query that behaves like a virtual table. This project uses v_results and v_attendance."),
      (["er diagram", "entity", "relationship"], "An <b>ER diagram</b> models entities (tables), attributes (columns) and relationships (1-1, 1-N, M-N). M-N relationships become a separate junction table."),
      (["sql", "dbms"], "<b>DBMS</b> software manages databases; <b>SQL</b> is the language: DDL (CREATE, ALTER), DML (INSERT, UPDATE, DELETE), DQL (SELECT), DCL (GRANT, REVOKE), TCL (COMMIT, ROLLBACK)."),
      (["deadlock"], "A <b>deadlock</b> happens when processes wait for each other forever. It needs mutual exclusion, hold-and-wait, no preemption and circular wait. Prevent by breaking one of them."),
      (["process", "thread"], "A <b>process</b> is a program in execution with its own memory; a <b>thread</b> is a lightweight unit inside a process that shares its memory."),
      (["osi", "tcp", "udp"], "<b>TCP</b> is connection-oriented and reliable (web, email); <b>UDP</b> is connectionless and faster (video, DNS). The OSI model has 7 layers: Physical, Data link, Network, Transport, Session, Presentation, Application."),
      (["sdlc", "agile", "waterfall"], "<b>SDLC</b> phases: requirements, design, implementation, testing, deployment, maintenance. Waterfall is sequential; Agile works in short iterations (sprints) with continuous feedback."),
      (["regression", "machine learning", "ai"], "<b>Linear regression</b> fits a line/plane that predicts a number from inputs. The Marks Predictor in this system is a multiple linear regression on 8 student factors.")]
CAREERS = [("Data Engineer / Database Administrator", ["database", "data"], "data", ["Advanced SQL & query tuning", "Data warehousing (ETL)", "Cloud databases (BigQuery, RDS)", "Python for data"]),
           ("Software Developer", ["software", "programming", "algorithm"], "software", ["Data structures & algorithms", "Git & system design basics", "A web framework (Django / React)", "Unit testing"]),
           ("Systems / Cloud / DevOps Engineer", ["operating", "system"], "systems", ["Linux administration", "Docker & Kubernetes", "CI/CD pipelines", "AWS / Azure fundamentals"]),
           ("Network & Security Engineer", ["network", "security"], "networking", ["CCNA fundamentals", "Network security basics", "Wireshark & packet analysis", "Ethical hacking intro"]),
           ("Data Scientist / ML Engineer", ["database", "math", "statistic", "data"], "ai", ["Python + pandas / NumPy", "Statistics & linear algebra", "scikit-learn & model evaluation", "Kaggle projects"]),
           ("Higher Studies / Research (M.Tech, MS)", [], "research", ["GATE / GRE preparation", "Reading research papers", "A mini research project", "Strong core fundamentals"])]
CLASS_Q = ("SELECT sb.subject_name, ROUND(AVG(r.pct),1) avg, ROUND(100.0*SUM(r.pct>=40)/COUNT(r.pct),1) pass, COALESCE(SUM(r.pct<40),0) b0, COALESCE(SUM(r.pct>=40 AND r.pct<60),0) b1, "
           "COALESCE(SUM(r.pct>=60 AND r.pct<80),0) b2, COALESCE(SUM(r.pct>=80),0) b3, (SELECT ROUND(AVG(pct),1) FROM v_attendance WHERE subject_id=sb.subject_id) att "
           "FROM subjects sb LEFT JOIN v_results r ON r.subject_id=sb.subject_id WHERE ?=1 OR sb.subject_id IN (SELECT subject_id FROM faculty_subjects WHERE faculty_id=?) GROUP BY sb.subject_id")

def corr(x, y):
    n = len(x)
    if n < 3: return 0
    mx, my = sum(x) / n, sum(y) / n; sx, sy = sum((a - mx) ** 2 for a in x) ** .5, sum((b - my) ** 2 for b in y) ** .5
    return round(sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy), 2) if sx and sy else 0

def scan(c, adm, fid, sub=None):  # attendance insights: detect <75% and notify students
    n = 0
    for r in c.execute("SELECT a.pct, sb.subject_name, x.user_id FROM v_attendance a JOIN subjects sb ON sb.subject_id=a.subject_id JOIN students x ON x.student_id=a.student_id WHERE a.pct<75 AND a.total>=5 "
                       "AND (?=1 OR a.subject_id IN (SELECT subject_id FROM faculty_subjects WHERE faculty_id=?)) AND (? IS NULL OR a.subject_id=?)", (adm, fid, sub, sub)).fetchall():
        msg = f"⚠ Low attendance in {r[1]}: {r[0]}% (minimum 75% required)"
        if not c.execute("SELECT 1 FROM notifications WHERE user_id=? AND message=?", (r[2], msg)).fetchone():
            c.execute("INSERT INTO notifications(user_id,message) VALUES(?,?)", (r[2], msg)); n += 1
    return n

def doubt(c, st, q):  # offline AI doubt solver: personal-data answers + subject knowledge base
    ql, sid, out = q.lower(), st["student_id"], []
    if "attend" in ql:
        r = Q(c, "SELECT sb.subject_name n, a.pct FROM v_attendance a JOIN subjects sb ON sb.subject_id=a.subject_id WHERE a.student_id=?", sid)
        out.append("Your attendance: " + ", ".join(f"{x['n']} {x['pct']}%" for x in r) + ". Keep every subject above 75%.")
    if re.search(r"\b(mark|result|score|grade|percent)", ql): s = stats(c, sid); out.append(f"Your current marks average is {s['marks']}% and your last exam was {s['last']}%.")
    if re.search(r"\b(predict|expect|future)", ql): out.append(f"The AI predicts your final percentage at {stats(c, sid)['predicted']}%. Open the Marks Predictor to try what-if values.")
    if "fee" in ql: out.append(f"Your pending fees: Rs {c.execute('SELECT COALESCE(SUM(amount),0) FROM fees WHERE student_id=? AND status=?', (sid, 'pending')).fetchone()[0]:.0f}.")
    if "exam" in ql:
        r = Q(c, "SELECT e.exam_type, e.exam_date, sb.subject_name FROM exams e JOIN subjects sb ON sb.subject_id=e.subject_id WHERE sb.course_id=? AND sb.semester=? AND e.exam_date>=date('now') ORDER BY e.exam_date LIMIT 3", st["course_id"], st["semester"])
        out.append("Upcoming exams: " + ("; ".join(f"{x['subject_name']} {x['exam_type']} on {x['exam_date']}" for x in r) or "none scheduled") + ".")
    if "plan" in ql: out.append("Open the <b>Study Plan</b> tab for your personalized weekly schedule.")
    if "career" in ql: out.append("Open the <b>Career Guidance</b> tab for suggested career paths.")
    out += [a for ks, a in KB if any(re.search(r"\b" + re.escape(k), ql) for k in ks)][:2]
    return "<br><br>".join(out) or "I couldn't find that yet. I can explain DBMS (normalization, ACID, joins, keys, indexing, triggers), OS, networks and SDLC topics, or tell you about your attendance, marks, fees and exams."

def study_plan(c, st):
    sid = st["student_id"]; weekly = max(6, round((st["study_hours"] or 2) * 7))
    rows = Q(c, "SELECT sb.subject_name name, (SELECT AVG(pct) FROM v_results WHERE student_id=? AND subject_id=sb.subject_id) m, (SELECT pct FROM v_attendance WHERE student_id=? AND subject_id=sb.subject_id) a FROM subjects sb WHERE sb.course_id=? AND sb.semester=?", sid, sid, st["course_id"], st["semester"])
    for r in rows:
        m, a = r["m"] or 0, r["a"] or 0; r["m"], r["a"] = round(m), round(a); r["need"] = max(5, 100 - m) + (20 if a < 75 else 0); r["topics"] = topics_for(r["name"])
        r["priority"] = "High" if m < 50 or a < 75 else "Medium" if m < 70 else "Low"
        r["action"] = ("Revise fundamentals + solve previous papers" if m < 50 else "Practice problems & mock tests" if m < 70 else "Maintain with weekly revision") + ("; fix attendance" if a < 75 else "")
    tot = sum(r["need"] for r in rows) or 1
    for r in rows: r["hours"] = round(weekly * r["need"] / tot, 1)
    rows.sort(key=lambda r: -r["need"]); days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]; sl = {d: [] for d in days}
    for i, r in enumerate(rows):
        n = max(1, round(r["hours"] / 1.5)); per = round(r["hours"] / n, 1)
        for k in range(n): sl[days[(i * 2 + k * 3) % 7]].append(f"{r['name']} - {per}h - {r['topics'][k % len(r['topics'])]}")
    s = stats(c, sid); tips = [f"At your current level the AI predicts {s['predicted']}%. Following this plan can lift it."]
    if s["att"] < 75: tips.append("Raise attendance above 75% - it directly improves your predicted score.")
    if s["backlogs"]: tips.append("Clear your backlog subjects first; they weigh heavily on the prediction.")
    if s["part"] < 5: tips.append("Participate more in class (ask questions, join discussions).")
    if s["study"] < 2.5: tips.append("Increase daily study time to at least 2.5 hours.")
    return {"weekly": weekly, "subjects": rows, "schedule": [{"day": d, "items": sl[d]} for d in days], "tips": tips}

def career(c, st, interest):
    s = stats(c, st["student_id"]); out = []
    sm = Q(c, "SELECT sb.subject_name n, AVG(r.pct) m FROM v_results r JOIN subjects sb ON sb.subject_id=r.subject_id WHERE r.student_id=? GROUP BY r.subject_id", st["student_id"])
    for title, kws, key, skills in CAREERS:
        hit = [x for x in sm if any(k in x["n"].lower() for k in kws)]; base = sum(x["m"] for x in hit) / len(hit) if hit else s["marks"]
        fit = .55 * base + .25 * s["predicted"] + s["part"] + (15 if interest == key else 0) + (8 if key == "research" and s["prev"] >= 75 else 0)
        why = (f"You score {round(base)}% in " + ", ".join(x["n"] for x in hit) if hit else f"Based on your overall marks ({round(s['marks'])}%) and predicted {s['predicted']}%") + (". Matches your stated interest." if interest == key else ".")
        out.append({"title": title, "fit": round(min(99, fit)), "why": why, "skills": skills})
    return sorted(out, key=lambda x: -x["fit"])[:3]

def class_ai(c, adm, fid):  # class performance analysis + remedial suggestions
    out = []
    for sb in Q(c, "SELECT DISTINCT sb.subject_id, sb.subject_name, fs.section FROM faculty_subjects fs JOIN subjects sb ON sb.subject_id=fs.subject_id WHERE ?=1 OR fs.faculty_id=?", adm, fid):
        ms, ats, pr, weak = [], [], [], []; tp = topics_for(sb["subject_name"])
        for x in Q(c, "SELECT x.student_id, u.full_name FROM students x JOIN subjects sj ON sj.course_id=x.course_id AND sj.semester=x.semester JOIN users u ON u.user_id=x.user_id WHERE sj.subject_id=? AND x.section=?", sb["subject_id"], sb["section"]):
            m = c.execute("SELECT AVG(pct) FROM v_results WHERE student_id=? AND subject_id=?", (x["student_id"], sb["subject_id"])).fetchone()[0]
            a = (c.execute("SELECT pct FROM v_attendance WHERE student_id=? AND subject_id=?", (x["student_id"], sb["subject_id"])).fetchone() or [None])[0]; s = stats(c, x["student_id"]); why = []
            if m is not None: ms.append(m)
            if a is not None: ats.append(a)
            pr.append(s["predicted"])
            if m is not None and m < 40: why.append(f"failing this subject ({round(m)}%)")
            elif m is not None and m < 55: why.append(f"low subject marks ({round(m)}%)")
            if a is not None and a < 75: why.append(f"attendance {a}%")
            if s["predicted"] < 50: why.append(f"predicted final {s['predicted']}%")
            if s["backlogs"]: why.append(f"{int(s['backlogs'])} backlog(s)")
            if s["part"] < 4: why.append("low participation")
            if why: weak.append({"name": x["full_name"], "reasons": why, "action": ("Counsel on attendance. " if a is not None and a < 75 else "") + (f"Remedial session on {tp[0]} / {tp[1]}." if m is not None and m < 55 else "Monitor progress and give extra practice.")})
        avg = round(sum(ms) / len(ms), 1) if ms else 0; pas = round(100 * sum(1 for m in ms if m >= 40) / len(ms), 1) if ms else 0; at = round(sum(ats) / len(ats), 1) if ats else 0
        tips = ([f"Class average is low ({avg}%): re-teach {', '.join(tp[:2])}."] if avg < 60 else []) + (["Attendance is low across the class - consider attendance reminders."] if at and at < 75 else []) + (["Pass rate is below 80% - hold a remedial session before the next exam."] if pas < 80 else [])
        out.append({"subject": sb["subject_name"], "section": sb["section"], "students": len(pr), "avg": avg, "pass": pas, "att": at, "pred_avg": round(sum(pr) / len(pr), 1) if pr else 0,
                    "dist": [sum(1 for m in ms if m < 40), sum(1 for m in ms if 40 <= m < 60), sum(1 for m in ms if 60 <= m < 80), sum(1 for m in ms if m >= 80)], "weak": weak, "tips": tips or ["Class is performing well - move on to advanced problems."]})
    return out

def ai_insights(c):
    rows = []
    for r in c.execute("SELECT s.student_id, u.full_name FROM students s JOIN users u ON u.user_id=s.user_id").fetchall(): d = stats(c, r[0]); d["name"] = r[1]; rows.append(d)
    if not rows: return {"insights": ["No data yet."], "at_risk": [], "top": [], "avg_pred": 0}
    risk = [{"name": d["name"], "predicted": d["predicted"], "att": d["att"], "backlogs": int(d["backlogs"]), "why": "; ".join(t for t, k in [(f"attendance {d['att']}%", d["att"] < 75), (f"predicted {d['predicted']}%", d["predicted"] < 50), (f"{int(d['backlogs'])} backlog(s)", d["backlogs"] > 0), ("low participation", d["part"] < 4)] if k)} for d in rows if d["att"] < 75 or d["predicted"] < 50]
    sub = Q(c, "SELECT sb.subject_name n, ROUND(AVG(r.pct),1) m FROM v_results r JOIN subjects sb ON sb.subject_id=r.subject_id GROUP BY r.subject_id ORDER BY m")
    ca, cs = corr([d["att"] for d in rows], [d["marks"] for d in rows]), corr([d["study"] for d in rows], [d["marks"] for d in rows]); ins = []
    if sub: ins += [f"Weakest subject: {sub[0]['n']} (average {sub[0]['m']}%) - consider remedial classes.", f"Strongest subject: {sub[-1]['n']} (average {sub[-1]['m']}%)."]
    ins += [f"{len(risk)} of {len(rows)} students are at risk (predicted below 50% or attendance below 75%).", f"Attendance vs marks correlation is {ca}" + (" - higher attendance clearly goes with higher marks." if ca > .5 else " - a weak relationship in this data."), f"Study hours vs marks correlation is {cs}."]
    lv, fd = c.execute("SELECT COUNT(*) FROM leave_requests WHERE status='pending'").fetchone()[0], c.execute("SELECT COUNT(*) FROM fees WHERE status='pending'").fetchone()[0]
    if lv: ins.append(f"{lv} leave request(s) are waiting for a decision.")
    if fd: ins.append(f"{fd} fee record(s) are still unpaid.")
    return {"insights": ins, "at_risk": risk, "avg_pred": round(sum(d["predicted"] for d in rows) / len(rows), 1), "top": sorted(rows, key=lambda d: -d["predicted"])[:3]}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, obj, ct="application/json"):
        b = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): self.route("GET")
    def do_POST(self): self.route("POST")
    def do_DELETE(self): self.route("DELETE")
    def route(self, m):
        p = self.path.split("?")[0]
        if m == "GET" and p == "/": return self.send(200, PAGE, "text/html; charset=utf-8")
        if m == "GET" and p.startswith("/files/"):
            fp = os.path.join("uploads", os.path.basename(p))
            if not os.path.exists(fp): return self.send(404, {"error": "File not found"})
            data = open(fp, "rb").read(); self.send_response(200); self.send_header("Content-Type", mimetypes.guess_type(fp)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); return self.wfile.write(data)
        c = db()
        try: self.api(m, p, c, json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")); c.commit()
        except sqlite3.IntegrityError as e: self.send(400, {"error": "Database constraint failed: " + str(e)})
        except Exception as e: self.send(400, {"error": str(e)})
        finally: c.close()

    def api(self, m, p, c, b):
        if p == "/api/login":
            u = c.execute("SELECT * FROM users WHERE email=? AND password_hash=?", (b["email"].strip().lower(), H(b["password"]))).fetchone()
            if not u: return self.send(401, {"error": "Invalid credentials"})
            if u["status"] != "active": return self.send(403, {"error": "Account is blocked"})
            t = secrets.token_hex(16); SESSIONS[t] = (u["user_id"], u["role"]); c.execute("UPDATE users SET last_login=CURRENT_TIMESTAMP WHERE user_id=?", (u["user_id"],)); return self.send(200, {"token": t, "role": u["role"], "name": u["full_name"]})
        me = SESSIONS.get(self.headers.get("Authorization", ""))
        if not me: return self.send(401, {"error": "Please login"})
        uid, role = me; ok = lambda: self.send(200, {"ok": True}); S = lambda x: self.send(200, x)
        adm, fac, stu = role == "admin", role == "faculty", role == "student"
        fid = (c.execute("SELECT faculty_id FROM faculty WHERE user_id=?", (uid,)).fetchone() or [None])[0]
        st = (Q(c, "SELECT * FROM students WHERE user_id=?", uid) or [None])[0]
        own = lambda sub: adm or c.execute("SELECT 1 FROM faculty_subjects WHERE faculty_id=? AND subject_id=?", (fid, sub)).fetchone()
        if p == "/api/password":
            if not c.execute("SELECT 1 FROM users WHERE user_id=? AND password_hash=?", (uid, H(b["old"]))).fetchone(): return self.send(400, {"error": "Old password is wrong"})
            c.execute("UPDATE users SET password_hash=? WHERE user_id=?", (H(b["new"]), uid)); return ok()
        if p == "/api/predict": return S(dict(zip(("prediction", "weights"), predict(*[float(b[k]) for k in ("attendance", "study_hours", "assign", "marks", "last_exam", "prev_sem", "backlogs", "participation")]))))
        if p == "/api/notifications": return S(Q(c, "SELECT * FROM notifications WHERE user_id=? ORDER BY notification_id DESC LIMIT 8", uid))
        if p == "/api/announcements" and m == "GET":
            aud = {"admin": "'all','students','faculty'", "faculty": "'all','faculty'", "student": "'all','students'"}[role]
            return S(Q(c, f"SELECT a.*, u.full_name author FROM announcements a LEFT JOIN users u ON u.user_id=a.created_by WHERE audience IN ({aud}) ORDER BY announcement_id DESC"))
        if p == "/api/announcements" and not stu: c.execute("INSERT INTO announcements(created_by,title,message,audience) VALUES(?,?,?,?)", (uid, b["title"], b["message"], b["audience"])); return ok()
        if p == "/api/timetable":
            w, a = ("1=1", ()) if adm else (("t.faculty_id=?", (fid,)) if fac else ("sb.course_id=? AND sb.semester=? AND t.section=?", (st["course_id"], st["semester"], st["section"])))
            return S(Q(c, f"SELECT t.*, sb.subject_name, u.full_name faculty FROM timetable t JOIN subjects sb ON sb.subject_id=t.subject_id JOIN faculty f ON f.faculty_id=t.faculty_id JOIN users u ON u.user_id=f.user_id WHERE {w} ORDER BY instr('MonTueWedThuFriSat',t.day), t.start_time", *a))
        if p == "/api/materials" and m == "GET":
            w, a = ("1=1", ()) if adm else (("mt.faculty_id=?", (fid,)) if fac else ("sb.course_id=? AND sb.semester=?", (st["course_id"], st["semester"])))
            return S(Q(c, f"SELECT mt.*, sb.subject_name FROM materials mt JOIN subjects sb ON sb.subject_id=mt.subject_id WHERE {w}", *a))
        if p == "/api/assignments" and m == "GET":
            if stu: return S(Q(c, "SELECT a.*, sb.subject_name, s.submission_id, s.marks, s.file_url FROM assignments a JOIN subjects sb ON sb.subject_id=a.subject_id LEFT JOIN submissions s ON s.assignment_id=a.assignment_id AND s.student_id=? WHERE sb.course_id=? AND sb.semester=?", st["student_id"], st["course_id"], st["semester"]))
            return S(Q(c, "SELECT a.*, sb.subject_name FROM assignments a JOIN subjects sb ON sb.subject_id=a.subject_id WHERE ? OR a.faculty_id=?", adm, fid))
        if p == "/api/upload":
            fn_ = secrets.token_hex(4) + "_" + re.sub(r"[^A-Za-z0-9._-]", "_", b["name"])[:60]; os.makedirs("uploads", exist_ok=True)
            open(os.path.join("uploads", fn_), "wb").write(base64.b64decode(b["data"])); return S({"url": "/files/" + fn_})
        if p == "/api/exams" and m == "GET":
            if stu: return S(Q(c, "SELECT e.*, sb.subject_name FROM exams e JOIN subjects sb ON sb.subject_id=e.subject_id WHERE sb.course_id=? AND sb.semester=? ORDER BY exam_date", st["course_id"], st["semester"]))
            return S(Q(c, "SELECT e.*, sb.subject_name FROM exams e JOIN subjects sb ON sb.subject_id=e.subject_id WHERE ?=1 OR e.subject_id IN (SELECT subject_id FROM faculty_subjects WHERE faculty_id=?) ORDER BY exam_date", adm, fid))
        if p == "/api/exams" and not stu:
            if not own(b["subject_id"]): return self.send(403, {"error": "Not your subject"})
            c.execute("INSERT INTO exams(subject_id,exam_type,exam_date,max_marks) VALUES(?,?,?,?)", (b["subject_id"], b["exam_type"], b["exam_date"], b["max_marks"])); return ok()
        if p == "/api/participation" and not stu:
            if not own(b["subject_id"]): return self.send(403, {"error": "Not your subject"})
            c.execute("UPDATE students SET participation=? WHERE student_id=?", (b["participation"], b["student_id"])); return ok()
        if p == "/api/fees" and stu: return S(Q(c, "SELECT * FROM fees WHERE student_id=? ORDER BY due_date", st["student_id"]))
        if p == "/api/leave" and stu:
            if m == "GET": return S(Q(c, "SELECT * FROM leave_requests WHERE student_id=? ORDER BY leave_id DESC", st["student_id"]))
            c.execute("INSERT INTO leave_requests(student_id,from_date,to_date,reason) VALUES(?,?,?,?)", (st["student_id"], b["from_date"], b["to_date"], b["reason"])); return ok()
        if p == "/api/leave" and not stu: return S(Q(c, f"SELECT l.*, u.full_name, x.enrollment_number FROM leave_requests l JOIN students x ON x.student_id=l.student_id JOIN users u ON u.user_id=x.user_id WHERE ?=1 OR l.student_id IN ({FAC_STUDENTS}) ORDER BY l.leave_id DESC", adm, fid))
        if p == "/api/leave_decide" and not stu:
            c.execute(f"UPDATE leave_requests SET status=? WHERE leave_id=? AND (?=1 OR student_id IN ({FAC_STUDENTS}))", (b["status"], b["leave_id"], adm, fid)); return ok()
        if p == "/api/fprofile" and fac: return S(Q(c, "SELECT f.*, u.full_name, u.email, d.department_name FROM faculty f JOIN users u ON u.user_id=f.user_id LEFT JOIN departments d ON d.department_id=f.department_id WHERE f.user_id=?", uid))
        if p == "/api/analytics":
            if stu:
                sid = st["student_id"]; return S({"stats": stats(c, sid), "trend": Q(c, "SELECT exam_type, ROUND(AVG(pct),1) avg FROM v_results WHERE student_id=? GROUP BY exam_type ORDER BY MIN(result_id)", sid),
                    "subjects": Q(c, "SELECT sb.subject_name name, ROUND(AVG(r.pct),1) marks, (SELECT pct FROM v_attendance WHERE student_id=? AND subject_id=sb.subject_id) att, (SELECT ROUND(AVG(pct),1) FROM v_results WHERE subject_id=sb.subject_id) class_avg FROM subjects sb LEFT JOIN v_results r ON r.subject_id=sb.subject_id AND r.student_id=? WHERE sb.course_id=? AND sb.semester=? GROUP BY sb.subject_id", sid, sid, st["course_id"], st["semester"])})
            return S({"subjects": Q(c, CLASS_Q, adm, fid)})
        if p == "/api/class_ai" and not stu: return S(class_ai(c, adm, fid))
        if p == "/api/ai_insights" and adm: return S(ai_insights(c))
        if p == "/api/alert_scan" and not stu: return S({"sent": scan(c, adm, fid)})
        if p == "/api/att_insights" and not stu:
            rows = Q(c, "SELECT u.full_name, sb.subject_name, a.present, a.total, a.pct FROM v_attendance a JOIN subjects sb ON sb.subject_id=a.subject_id JOIN students x ON x.student_id=a.student_id JOIN users u ON u.user_id=x.user_id WHERE a.pct<75 AND (?=1 OR a.subject_id IN (SELECT subject_id FROM faculty_subjects WHERE faculty_id=?)) ORDER BY a.pct", adm, fid)
            for r in rows: r["need"] = max(0, math.ceil((.75 * r["total"] - r["present"]) / .25))
            return S(rows)
        if stu and p == "/api/study_plan": return S(study_plan(c, st))
        if stu and p == "/api/doubt": return S({"answer": doubt(c, st, b["question"])})
        if stu and p == "/api/career": return S(career(c, st, b.get("interest")))
        # ---------------- student ----------------
        if stu and p == "/api/me":
            prof = Q(c, "SELECT s.*, u.full_name, u.email, co.course_name, d.department_name FROM students s JOIN users u ON u.user_id=s.user_id JOIN courses co ON co.course_id=s.course_id JOIN departments d ON d.department_id=co.department_id WHERE s.student_id=?", st["student_id"])[0]
            return S({"profile": prof, "stats": stats(c, st["student_id"]), "results": Q(c, "SELECT * FROM v_results WHERE student_id=?", st["student_id"]),
                      "attendance": Q(c, "SELECT sb.subject_name, sb.subject_code, a.total, a.present, a.pct FROM subjects sb LEFT JOIN v_attendance a ON a.subject_id=sb.subject_id AND a.student_id=? WHERE sb.course_id=? AND sb.semester=?", st["student_id"], st["course_id"], st["semester"])})
        if stu and p == "/api/submit":
            c.execute("INSERT INTO submissions(assignment_id,student_id,file_url) VALUES(?,?,?) ON CONFLICT(assignment_id,student_id) DO UPDATE SET file_url=excluded.file_url", (b["assignment_id"], st["student_id"], b.get("file") or b.get("link") or "")); return ok()
        # ---------------- faculty & admin ----------------
        if fac or adm:
            if p == "/api/fsubjects": return S(Q(c, "SELECT sb.subject_id, sb.subject_name, sb.subject_code, fs.section, (SELECT COUNT(*) FROM students x WHERE x.course_id=sb.course_id AND x.semester=sb.semester AND x.section=fs.section) n FROM faculty_subjects fs JOIN subjects sb ON sb.subject_id=fs.subject_id WHERE fs.faculty_id=?", fid))
            g = re.match(r"/api/roster/(\d+)$", p)
            if g:
                if not own(g[1]): return self.send(403, {"error": "Not your subject"})
                return S(Q(c, "SELECT x.student_id, x.enrollment_number, u.full_name, x.participation, x.backlogs, (SELECT pct FROM v_attendance WHERE student_id=x.student_id AND subject_id=sb.subject_id) att, (SELECT ROUND(AVG(pct),1) FROM v_results WHERE student_id=x.student_id AND subject_id=sb.subject_id) avg "
                           "FROM subjects sb JOIN faculty_subjects fs ON fs.subject_id=sb.subject_id AND fs.faculty_id=? JOIN students x ON x.course_id=sb.course_id AND x.semester=sb.semester AND x.section=fs.section JOIN users u ON u.user_id=x.user_id WHERE sb.subject_id=?", fid, g[1]))
            if p == "/api/attendance":
                if not own(b["subject_id"]): return self.send(403, {"error": "Not your subject"})
                for s, v in b["records"]: c.execute("INSERT INTO attendance(student_id,subject_id,faculty_id,date,status) VALUES(?,?,?,?,?) ON CONFLICT(student_id,subject_id,date) DO UPDATE SET status=excluded.status", (s, b["subject_id"], fid, b["date"], v))
                scan(c, 1, None, b["subject_id"]); return ok()
            if p == "/api/marks":
                if not own(b["subject_id"]): return self.send(403, {"error": "Not your subject"})
                c.execute("INSERT INTO results(student_id,subject_id,exam_type,marks,max_marks) VALUES(?,?,?,?,?) ON CONFLICT(student_id,subject_id,exam_type) DO UPDATE SET marks=excluded.marks, max_marks=excluded.max_marks", (b["student_id"], b["subject_id"], b["exam_type"], b["marks"], b["max_marks"])); return ok()
            if p == "/api/materials" and fac:
                if not own(b["subject_id"]): return self.send(403, {"error": "Not your subject"})
                c.execute("INSERT INTO materials(subject_id,faculty_id,title,url) VALUES(?,?,?,?)", (b["subject_id"], fid, b["title"], b.get("file") or b.get("link") or "")); return ok()
            if p == "/api/assignments" and fac:
                if not own(b["subject_id"]): return self.send(403, {"error": "Not your subject"})
                c.execute("INSERT INTO assignments(subject_id,faculty_id,title,description,deadline) VALUES(?,?,?,?,?)", (b["subject_id"], fid, b["title"], b["description"], b["deadline"])); return ok()
            g = re.match(r"/api/submissions/(\d+)$", p)
            if g:
                if not c.execute("SELECT 1 FROM assignments WHERE assignment_id=? AND (?=1 OR faculty_id=?)", (g[1], adm, fid)).fetchone(): return self.send(403, {"error": "Not your assignment"})
                return S(Q(c, "SELECT s.*, u.full_name FROM submissions s JOIN students x ON x.student_id=s.student_id JOIN users u ON u.user_id=x.user_id WHERE s.assignment_id=?", g[1]))
            if p == "/api/grade_sub" and fac:
                c.execute("UPDATE submissions SET marks=? WHERE submission_id=? AND assignment_id IN (SELECT assignment_id FROM assignments WHERE faculty_id=?)", (b["marks"], b["submission_id"], fid)); return ok()
            if p == "/api/reports":
                ids = Q(c, "SELECT student_id FROM students" if adm else "SELECT DISTINCT x.student_id FROM faculty_subjects fs JOIN subjects sb ON sb.subject_id=fs.subject_id JOIN students x ON x.course_id=sb.course_id AND x.semester=sb.semester AND x.section=fs.section WHERE fs.faculty_id=?", *([] if adm else [fid]))
                rows = []
                for r in ids:
                    s = stats(c, r["student_id"]); s.update(Q(c, "SELECT u.full_name name, x.enrollment_number en FROM students x JOIN users u ON u.user_id=x.user_id WHERE x.student_id=?", r["student_id"])[0]); rows.append(s)
                return S({"students": rows, "subjects": Q(c, "SELECT subject_name, ROUND(AVG(pct),1) avg, ROUND(100.0*SUM(pct>=40)/COUNT(*),1) pass_pct FROM v_results GROUP BY subject_id") if adm else []})
        if not adm: return self.send(403, {"error": "Access denied for your role"})
        # ---------------- admin only ----------------
        if p == "/api/meta": return S({k: Q(c, q) for k, q in {"depts": "SELECT * FROM departments", "courses": "SELECT * FROM courses", "subjects": "SELECT * FROM subjects",
                                    "faculty": "SELECT f.faculty_id, u.full_name FROM faculty f JOIN users u ON u.user_id=f.user_id", "students": "SELECT s.student_id, s.enrollment_number||' - '||u.full_name AS label FROM students s JOIN users u ON u.user_id=s.user_id"}.items()})
        if p == "/api/stats": return S(Q(c, "SELECT (SELECT COUNT(*) FROM students) students,(SELECT COUNT(*) FROM faculty) faculty,(SELECT COUNT(*) FROM courses) courses,(SELECT COUNT(*) FROM subjects) subjects,"
                                         "(SELECT ROUND(AVG(100.0*(status='P')),1) FROM attendance) att,(SELECT COUNT(*) FROM assignments) assignments,(SELECT COUNT(*) FROM leave_requests WHERE status='pending') pending_leaves,(SELECT COALESCE(SUM(amount),0) FROM fees WHERE status='pending') fees_due")[0])
        if p == "/api/students" and m == "GET": return S(Q(c, "SELECT s.*, u.full_name, u.email, co.course_name FROM students s JOIN users u ON u.user_id=s.user_id LEFT JOIN courses co ON co.course_id=s.course_id ORDER BY s.enrollment_number"))
        if p == "/api/students":
            u = mku(c, b["full_name"], b["email"].lower(), b.get("password") or "student123", "student")
            c.execute("INSERT INTO students(user_id,enrollment_number,course_id,semester,section,study_hours,prev_sem_percent,backlogs,participation) VALUES(?,?,?,?,?,?,?,?,?)", (u, b["enrollment_number"], b["course_id"], b["semester"] or 1, b["section"] or "A", nz(b, "study_hours", 2), nz(b, "prev_sem_percent", 60), nz(b, "backlogs", 0), nz(b, "participation", 5))); return ok()
        if p == "/api/faculty" and m == "GET": return S(Q(c, "SELECT f.*, u.full_name, u.email, d.department_name FROM faculty f JOIN users u ON u.user_id=f.user_id LEFT JOIN departments d ON d.department_id=f.department_id"))
        if p == "/api/faculty":
            u = mku(c, b["full_name"], b["email"].lower(), b.get("password") or "faculty123", "faculty")
            c.execute("INSERT INTO faculty(user_id,employee_code,department_id,designation,qualification,experience_years,specialization) VALUES(?,?,?,?,?,?,?)", (u, b["employee_code"], b["department_id"], b.get("designation"), b.get("qualification"), nz(b, "experience_years", 0), b.get("specialization"))); return ok()
        g = re.match(r"/api/users/(\d+)$", p)
        if g and m == "DELETE": c.execute("DELETE FROM users WHERE user_id=? AND role!='admin'", (g[1],)); return ok()
        if p == "/api/departments": c.execute("INSERT INTO departments(department_name,department_code) VALUES(?,?)", (b["department_name"], b["department_code"])); return ok()
        if p == "/api/courses": c.execute("INSERT INTO courses(course_name,course_code,department_id) VALUES(?,?,?)", (b["course_name"], b["course_code"], b["department_id"])); return ok()
        if p == "/api/subjects" and m == "GET": return S(Q(c, "SELECT sb.*, co.course_name, (SELECT GROUP_CONCAT(u.full_name||' ('||fs.section||')') FROM faculty_subjects fs JOIN faculty f ON f.faculty_id=fs.faculty_id JOIN users u ON u.user_id=f.user_id WHERE fs.subject_id=sb.subject_id) faculty FROM subjects sb JOIN courses co ON co.course_id=sb.course_id"))
        if p == "/api/subjects": c.execute("INSERT INTO subjects(subject_name,subject_code,course_id,semester) VALUES(?,?,?,?)", (b["subject_name"], b["subject_code"], b["course_id"], b["semester"])); return ok()
        if p == "/api/assign": c.execute("INSERT INTO faculty_subjects VALUES(?,?,?)", (b["faculty_id"], b["subject_id"], b["section"] or "A")); return ok()
        if p == "/api/timetable": c.execute("INSERT INTO timetable(subject_id,faculty_id,section,day,start_time,end_time) VALUES(?,?,?,?,?,?)", (b["subject_id"], b["faculty_id"], b["section"] or "A", b["day"], b["start_time"], b["end_time"])); return ok()
        if p == "/api/users" and m == "GET": return S(Q(c, "SELECT user_id, full_name, email, role, status, created_at, last_login FROM users"))
        if p == "/api/user_update":
            if b.get("password"): c.execute("UPDATE users SET password_hash=? WHERE user_id=?", (H(b["password"]), b["user_id"]))
            if b.get("status"): c.execute("UPDATE users SET status=? WHERE user_id=? AND role!='admin'", (b["status"], b["user_id"]))
            return ok()
        if p == "/api/fees" and m == "GET": return S(Q(c, "SELECT f.*, u.full_name, x.enrollment_number FROM fees f JOIN students x ON x.student_id=f.student_id JOIN users u ON u.user_id=x.user_id ORDER BY f.fee_id DESC"))
        if p == "/api/fees": c.execute("INSERT INTO fees(student_id,description,amount,due_date) VALUES(?,?,?,?)", (b["student_id"], b["description"], b["amount"], b["due_date"])); return ok()
        if p == "/api/fee_pay": c.execute("UPDATE fees SET status='paid', payment_date=date('now') WHERE fee_id=?", (b["fee_id"],)); return ok()
        if p == "/api/audit": return S(Q(c, "SELECT * FROM audit_logs ORDER BY log_id DESC LIMIT 60"))
        if p == "/api/sql":
            q = b["query"].strip().rstrip(";")
            if not re.match(r"(?i)^select\b", q) or ";" in q: return self.send(400, {"error": "Only a single SELECT query is allowed"})
            cur = c.execute(q); rows = cur.fetchmany(200); return S({"cols": [x[0] for x in cur.description], "rows": [list(r) for r in rows]})
        self.send(404, {"error": "Not found"})

PAGE = r'''<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Student Management System</title><style>
:root{--p:#1e40af;--m:#64748b}*{box-sizing:border-box;font-family:system-ui,sans-serif}body{margin:0;background:#f1f5f9;color:#0f172a}
.login{min-height:100vh;display:grid;place-items:center;background:linear-gradient(135deg,#1e3a8a,#7c3aed)}.login .card{width:360px}
.card{background:#fff;border-radius:14px;padding:18px;box-shadow:0 4px 18px #0001;margin-bottom:14px}h2,h3{margin:0 0 10px}
input,select{width:100%;padding:9px;border:1px solid #cbd5e1;border-radius:8px;margin:4px 0 8px;font-size:14px}
button{background:var(--p);color:#fff;border:0;padding:8px 13px;border-radius:8px;cursor:pointer;font-weight:600;margin:2px}button.g{background:#e2e8f0;color:#0f172a}button.r{background:#ef4444;padding:4px 9px}
nav{background:#fff;display:flex;gap:2px;padding:8px 14px;align-items:center;box-shadow:0 1px 6px #0001;flex-wrap:wrap}nav b{margin-right:14px;color:var(--p)}nav button{background:none;color:var(--m);padding:7px 10px}nav button.on{background:var(--p);color:#fff}
main{max-width:1150px;margin:16px auto;padding:0 12px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:14px}
.stat{background:#fff;border-radius:14px;padding:15px;box-shadow:0 4px 18px #0001}.stat span{color:var(--m);font-size:13px}.stat h2{font-size:26px;margin:4px 0 0;color:var(--p)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px;text-align:left;border-bottom:1px solid #e2e8f0}th{color:var(--m)}td input,td select{margin:0;padding:5px;width:80px}
.bar{background:#e2e8f0;border-radius:6px;height:9px}.bar i{display:block;height:9px;border-radius:6px;background:var(--p)}.big{font-size:52px;font-weight:800;color:var(--p)}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.hide{display:none}.ov{overflow-x:auto}pre{background:#0f172a;color:#a5f3fc;padding:10px;border-radius:8px;overflow:auto;font-size:12px}
.tag{padding:2px 8px;border-radius:10px;font-size:12px;background:#fee2e2;color:#b91c1c}.ok{background:#dcfce7;color:#166534}</style></head><body>
<div id="L" class="login"><div class="card"><h2>🎓 Student Management System</h2><p style="color:var(--m)">One login · Role-based access</p>
<input id="u" placeholder="Email"><input id="p" type="password" placeholder="Password" onkeydown="if(event.key=='Enter')login()">
<button style="width:100%" onclick="login()">Login</button><p id="e" style="color:#ef4444"></p>
<small style="color:var(--m)">admin@uni.edu / admin123<br>f1@uni.edu / faculty123<br>s1@uni.edu / student123</small></div></div>
<div id="A" class="hide"><nav id="nav"></nav><main id="M"></main></div><script>
let T='',R='',META={},ME,FS=[];const $=i=>document.getElementById(i),err=e=>alert(e.message),today=new Date().toISOString().slice(0,10);
async function api(p,m='GET',b){const r=await fetch(p,{method:m,headers:{Authorization:T,'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});const d=await r.json();if(!r.ok)throw new Error(d.error);return d}
const TABS={admin:{Dashboard:dash,Students:studs,Faculty:facs,'Courses & Subjects':acad,Timetable:tt,Reports:reports,Accounts:accts,Announcements:notes,Fees:fees,'Leave Requests':leaves,Exams:exams,'AI Insights':aiIns,Analytics:ana,'Attendance Insights':attIns,'Audit Logs':audit,'SQL Console':sql,'Marks Predictor':pred,Settings:sett},
faculty:{Dashboard:fdash,Profile:fprof,'My Classes':classes,Exams:exams,'Leave Requests':leaves,'Class Analysis':classAn,'AI Suggestions':aiSug,'Attendance Insights':attIns,Analytics:ana,Materials:mats,Assignments:fasg,Timetable:tt,Announcements:notes,Reports:reports,'Marks Predictor':pred,Settings:sett},
student:{Overview:sdash,Profile:prof,Attendance:att,Results:res,Exams:exams,Timetable:tt,Assignments:sasg,'Leave Requests':sleave,'Fee Details':sfee,Analytics:ana,'Study Plan':plan,'Doubt Solver':chat,'Career Guidance':career,Materials:mats,Announcements:notes,'Marks Predictor':pred,Settings:sett}};
async function login(){try{const d=await api('/api/login','POST',{email:$('u').value,password:$('p').value});T=d.token;R=d.role;$('L').classList.add('hide');$('A').classList.remove('hide');
if(R=='admin')await meta();if(R=='faculty')FS=await api('/api/fsubjects');const t=Object.keys(TABS[R]);
$('nav').innerHTML='<b>🎓 SMS · '+R.toUpperCase()+' · '+d.name+'</b>'+t.map(x=>`<button onclick="go('${x}',this)">${x}</button>`).join('')+'<button class="g" onclick="location.reload()">Logout</button>';go(t[0],$('nav').children[1])}catch(e){$('e').innerText=e.message}}
const meta=async()=>META=await api('/api/meta');
const upl=f=>new Promise((ok,no)=>{const r=new FileReader();r.onload=async()=>{try{ok((await api('/api/upload','POST',{name:f.name,data:r.result.split(',')[1]})).url)}catch(e){no(e)}};r.readAsDataURL(f)});
const stag=s=>`<span class="tag" style="${s=='approved'?'background:#dcfce7;color:#166534':s=='pending'?'background:#fef9c3;color:#854d0e':''}">${s}</span>`;
function go(t,b){[...$('nav').children].forEach(x=>x.classList.remove('on'));b.classList.add('on');TABS[R][t]()}
const bar=v=>`<div class="bar"><i style="width:${v||0}%"></i></div>`,opt=(a,v,l)=>a.map(x=>`<option value="${x[v]}">${typeof l=='function'?l(x):x[l]}</option>`).join('');
const tbl=(h,rows)=>`<div class="ov"><table><tr>${h.map(x=>'<th>'+x+'</th>').join('')}</tr>${rows.map(r=>'<tr>'+r.map(x=>'<td>'+(x??'-')+'</td>').join('')+'</tr>').join('')}</table></div>`;
const form=(t,f,path,cb)=>`<div class="card"><h3>${t}</h3><div class="row">${f.map(x=>`<div>${x[1]}${x[2].startsWith('<')?`<select id="f_${x[0]}">${x[2]}</select>`:`<input id="f_${x[0]}" type="${x[2]}" ${x[2]=='date'?`value="${today}"`:''}>`}</div>`).join('')}</div><button onclick="sub('${path}',[${f.map(x=>`'${x[0]}'`)}],${cb})">Save</button></div>`;
async function sub(p,k,cb){const b={};try{for(const x of k){const e=$('f_'+x);b[x]=e.type=='file'?(e.files[0]?await upl(e.files[0]):''):e.type=='number'?(e.value===''?null:+e.value):e.value}await api(p,'POST',b);if(R=='admin')await meta();cb()}catch(e){err(e)}}
async function del(u,cb){if(confirm('Delete this user and all linked records?')){await api('/api/users/'+u,'DELETE');cb()}}
const chart=(labels,series)=>`<div style="display:flex;gap:18px;align-items:flex-end;border-bottom:1px solid #cbd5e1;padding:20px 0 6px;overflow-x:auto">${labels.map((l,i)=>`<div style="text-align:center;min-width:100px"><div style="display:flex;gap:4px;align-items:flex-end;height:160px;justify-content:center">${series.map(s=>`<div title="${s.name}: ${s.v[i]??0}" style="width:24px;height:${s.v[i]||0}%;background:${s.c};border-radius:4px 4px 0 0;position:relative"><small style="position:absolute;top:-16px;left:-2px;font-size:10px">${s.v[i]??''}</small></div>`).join('')}</div><small>${l}</small></div>`).join('')}</div><p>${series.map(s=>`<span style="color:${s.c}">■</span> ${s.name}`).join(' &nbsp; ')}</p>`;
const distBar=(n,v)=>{const t=v.reduce((a,b)=>a+(b||0),0)||1,c=['#ef4444','#f59e0b','#3b82f6','#16a34a'];return `<p>${n}</p><div style="display:flex;height:22px;border-radius:6px;overflow:hidden;background:#e2e8f0">${v.map((x,i)=>`<div title="${x||0}" style="width:${100*(x||0)/t}%;background:${c[i]};color:#fff;font-size:11px;text-align:center">${x||''}</div>`).join('')}</div>`};
const tag=(v,lim)=>`<span class="tag ${v>=lim?'ok':''}">${v}%</span>`;
async function notifs(){const n=await api('/api/notifications');return `<div class="card"><h3>🔔 Notifications</h3>${n.map(x=>`<p>• ${x.message} <small style="color:var(--m)">${x.created_at}</small></p>`).join('')||'None'}</div>`}
// ---------- ADMIN ----------
async function dash(){const s=await api('/api/stats');$('M').innerHTML=`<div class="grid">${[['Students',s.students],['Faculty',s.faculty],['Courses',s.courses],['Subjects',s.subjects],['Avg Attendance',(s.att??0)+'%'],['Assignments',s.assignments],['Pending Leaves',s.pending_leaves],['Fees Due','₹'+s.fees_due]].map(x=>`<div class="stat"><span>${x[0]}</span><h2>${x[1]}</h2></div>`).join('')}</div>`+await notifs()}
async function studs(){const l=await api('/api/students');$('M').innerHTML=form('Add Student',[['full_name','Name','text'],['email','Email (login)','text'],['enrollment_number','Enrollment no','text'],['course_id','Course',opt(META.courses,'course_id','course_name')],['semester','Semester','number'],['section','Section','text'],['study_hours','Study hrs/day','number'],['prev_sem_percent','Previous semester %','number'],['backlogs','Backlogs','number'],['participation','Participation (0-10)','number'],['password','Password (default student123)','text']],'/api/students','studs')+
`<div class="card"><h3>Students</h3>${tbl(['Enroll','Name','Email','Course','Sem','Sec','Prev %','Backlogs',''],l.map(s=>[s.enrollment_number,s.full_name,s.email,s.course_name,s.semester,s.section,s.prev_sem_percent,s.backlogs,`<button class="r" onclick="del(${s.user_id},studs)">✕</button>`]))}</div>`}
async function facs(){const l=await api('/api/faculty');$('M').innerHTML=form('Add Faculty',[['full_name','Name','text'],['email','Email (login)','text'],['employee_code','Employee code','text'],['designation','Designation','text'],['qualification','Qualification','text'],['experience_years','Experience (years)','number'],['specialization','Specialization','text'],['department_id','Department',opt(META.depts,'department_id','department_name')],['password','Password (default faculty123)','text']],'/api/faculty','facs')+
`<div class="card"><h3>Faculty</h3>${tbl(['Code','Name','Designation','Qualification','Exp','Specialization','Department',''],l.map(f=>[f.employee_code,f.full_name,f.designation,f.qualification,f.experience_years+' yrs',f.specialization,f.department_name,`<button class="r" onclick="del(${f.user_id},facs)">✕</button>`]))}</div>`}
async function acad(){const l=await api('/api/subjects');$('M').innerHTML=form('Add Department',[['department_name','Name','text'],['department_code','Code','text']],'/api/departments','acad')+
form('Add Course',[['course_name','Name','text'],['course_code','Code','text'],['department_id','Department',opt(META.depts,'department_id','department_name')]],'/api/courses','acad')+
form('Add Subject',[['subject_name','Name','text'],['subject_code','Code','text'],['course_id','Course',opt(META.courses,'course_id','course_name')],['semester','Semester','number']],'/api/subjects','acad')+
form('Assign Faculty to Subject',[['faculty_id','Faculty',opt(META.faculty,'faculty_id','full_name')],['subject_id','Subject',opt(META.subjects,'subject_id','subject_name')],['section','Section','text']],'/api/assign','acad')+
`<div class="card"><h3>Subjects</h3>${tbl(['Code','Subject','Course','Sem','Faculty'],l.map(s=>[s.subject_code,s.subject_name,s.course_name,s.semester,s.faculty]))}</div>`}
async function tt(){const l=await api('/api/timetable');$('M').innerHTML=(R=='admin'?form('Add Timetable Slot',[['subject_id','Subject',opt(META.subjects,'subject_id','subject_name')],['faculty_id','Faculty',opt(META.faculty,'faculty_id','full_name')],['section','Section','text'],['day','Day','<option>Mon</option><option>Tue</option><option>Wed</option><option>Thu</option><option>Fri</option><option>Sat</option>'],['start_time','Start','time'],['end_time','End','time']],'/api/timetable','tt'):'')+
`<div class="card"><h3>🗓 Timetable</h3>${tbl(['Day','Time','Subject','Faculty','Section'],l.map(t=>[t.day,t.start_time+' - '+t.end_time,t.subject_name,t.faculty,t.section]))}</div>`}
async function reports(){const d=await api('/api/reports');$('M').innerHTML=(d.subjects.length?`<div class="card"><h3>📊 Subject analytics</h3>${tbl(['Subject','Avg %','Pass %'],d.subjects.map(s=>[s.subject_name,s.avg,s.pass_pct]))}</div>`:'')+
`<div class="card"><h3>🤖 Student performance + AI predicted percentage</h3>${tbl(['Enroll','Name','Att','Study','Assign','Current','Last exam','Prev sem','Backlogs','Partic.','Predicted %','Status'],d.students.map(s=>[s.en,s.name,s.att+'%',s.study,s.assign+'%',s.marks+'%',s.last+'%',s.prev+'%',s.backlogs,s.part,'<b>'+s.predicted+'%</b>',s.predicted<50||s.att<75?'<span class="tag">At risk</span>':'<span class="tag ok">On track</span>']))}</div>`}
async function accts(){const l=await api('/api/users');$('M').innerHTML=`<div class="card"><h3>Accounts & Roles</h3>${tbl(['Name','Email','Role','Status','Last login','Actions'],l.map(u=>[u.full_name,u.email,u.role,u.status,u.last_login,u.role=='admin'?'-':`<button class="g" onclick="us(${u.user_id},'${u.status=='active'?'blocked':'active'}')">${u.status=='active'?'Block':'Unblock'}</button><button class="g" onclick="rp(${u.user_id})">Reset password</button>`]))}</div>`}
async function us(i,s){await api('/api/user_update','POST',{user_id:i,status:s});accts()}async function rp(i){const p=prompt('New password');if(p){await api('/api/user_update','POST',{user_id:i,password:p});alert('Done')}}
async function audit(){const l=await api('/api/audit');$('M').innerHTML=`<div class="card"><h3>Audit Logs (written automatically by DB triggers)</h3>${tbl(['Time','Action'],l.map(a=>[a.ts,a.action]))}</div>`}
function sql(){$('M').innerHTML=`<div class="card"><h3>SQL Console (SELECT only)</h3><input id="q" value="SELECT * FROM v_results"><button onclick="runQ()">Run</button>
<button class="g" onclick="$('q').value='SELECT * FROM v_attendance'">v_attendance</button><button class="g" onclick="$('q').value='SELECT sb.subject_name, COUNT(*) n, ROUND(AVG(pct),1) avg FROM v_results r JOIN subjects sb USING(subject_id) GROUP BY subject_id'">GROUP BY</button><div id="qo"></div></div>`}
async function runQ(){try{const d=await api('/api/sql','POST',{query:$('q').value});$('qo').innerHTML=tbl(d.cols,d.rows)}catch(e){err(e)}}
// ---------- COMMON ----------
async function notes(){const l=await api('/api/announcements');$('M').innerHTML=(R!='student'?form('Post Announcement',[['title','Title','text'],['message','Message','text'],['audience','Audience','<option value="all">Everyone</option><option value="students">Students</option><option value="faculty">Faculty</option>']],'/api/announcements','notes'):'')+
`<div class="card"><h3>📢 Announcements</h3>${l.map(n=>`<p><b>${n.title}</b> <small style="color:var(--m)">by ${n.author} · ${n.created_at}</small><br>${n.message}</p>`).join('')}</div>`}
function sett(){$('M').innerHTML=form('Change Password',[['old','Old password','password'],['new','New password','password']],'/api/password','()=>alert("Password changed ✔")')}
async function mats(){const l=await api('/api/materials');$('M').innerHTML=(R=='faculty'?form('Upload Class Material (link)',[['subject_id','Subject',opt(FS,'subject_id','subject_name')],['title','Title','text'],['file','Upload PDF / notes','file'],['link','Or paste a link','text']],'/api/materials','mats'):'')+
`<div class="card"><h3>📚 Study Materials</h3>${tbl(['Subject','Title','Link'],l.map(m=>[m.subject_name,m.title,m.url?`<a href="${m.url}" target="_blank">Download</a>`:'-']))}</div>`}
async function pred(){let v={};if(R=='student')v=(await api('/api/me')).stats;
const f=[['pa','Attendance %',v.att],['ps','Study hours/day',v.study],['pg','Assignment score %',v.assign],['pk','Current marks % (all exams)',v.marks],['pl','Last exam marks %',v.last],['pv','Previous semester %',v.prev],['pb','Backlogs',v.backlogs],['pt','Participation (0-10)',v.part]];
$('M').innerHTML=`<div class="card"><h3>🤖 AI Marks Predictor</h3><p style="color:var(--m)">Predicts new marks from the last exam result plus 7 other factors (multiple linear regression trained on the <code>history</code> table).</p><div class="row">${f.map(x=>`<div>${x[1]}<input id="${x[0]}" type="number" value="${x[2]??''}"></div>`).join('')}</div><button onclick="runP()">Predict</button><div id="out"></div></div>`}
async function runP(){try{const g=i=>$(i).value;const d=await api('/api/predict','POST',{attendance:g('pa'),study_hours:g('ps'),assign:g('pg'),marks:g('pk'),last_exam:g('pl'),prev_sem:g('pv'),backlogs:g('pb'),participation:g('pt')});
const n=['attendance','study hrs','assignments','current marks','last exam','prev sem','backlogs','participation'];
$('out').innerHTML=`<p>Predicted new / final percentage</p><div class="big">${d.prediction}%</div>${bar(d.prediction)}<pre>y = ${d.weights[0]} `+d.weights.slice(1).map((w,i)=>`+ (${w} x ${n[i]})`).join(' ')+`</pre>`}catch(e){err(e)}}
// ---------- FACULTY ----------
async function fdash(){const l=await api('/api/timetable'),dn=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][new Date().getDay()],td=l.filter(x=>x.day==dn);
$('M').innerHTML=`<div class="card"><h3>Assigned Subjects & Classes</h3>${tbl(['Code','Subject','Section','Students'],FS.map(s=>[s.subject_code,s.subject_name,s.section,s.n]))}</div>
<div class="card"><h3>📅 Upcoming lectures - today (${dn})</h3>${td.length?tbl(['Time','Subject','Section'],td.map(x=>[x.start_time+' - '+x.end_time,x.subject_name,x.section])):'<p>No lectures today 🎉</p>'}</div>
<div class="card"><h3>This week</h3>${tbl(['Day','Time','Subject','Section'],l.map(x=>[x.day,x.start_time+' - '+x.end_time,x.subject_name,x.section]))}</div>`+await notifs()}
function classes(){$('M').innerHTML=`<div class="card"><h3>My Classes</h3>${FS.map(s=>`<button class="g" onclick="roster(${s.subject_id},'${s.subject_name}')">${s.subject_code} · ${s.subject_name}</button>`).join('')}</div><div id="ro"></div>`}
let CUR=0,RL=[];async function roster(id,t){CUR=id;RL=await api('/api/roster/'+id);$('ro').innerHTML=`<div class="card"><h3>${t}</h3><div class="row"><div>Date<input id="dt" type="date" value="${today}"></div><div>Exam type<select id="et"><option>Internal</option><option>Midterm</option><option>Final</option></select></div><div>Max marks<input id="mx" type="number" value="50"></div></div>
<div class="ov"><table><tr><th>Enroll</th><th>Name</th><th>Att %</th><th>Avg %</th><th>Backlogs</th><th>Today</th><th>Marks</th><th>Participation /10</th><th></th></tr>${RL.map(s=>`<tr><td>${s.enrollment_number}</td><td>${s.full_name}</td><td>${s.att??'-'}</td><td>${s.avg??'-'}</td><td>${s.backlogs}</td><td><select id="a${s.student_id}"><option>P</option><option>A</option></select></td><td><input id="m${s.student_id}" type="number"></td><td><input id="p${s.student_id}" type="number" value="${s.participation}"></td><td><button onclick="saveM(${s.student_id})">Save</button></td></tr>`).join('')}</table></div>
<button onclick="saveA()">Save attendance for date</button></div>`}
async function saveA(){try{await api('/api/attendance','POST',{subject_id:CUR,date:$('dt').value,records:RL.map(s=>[s.student_id,$('a'+s.student_id).value])});alert('Attendance saved ✔');roster(CUR,'')}catch(e){err(e)}}
async function saveM(s){try{const m=$('m'+s).value;if(m!=='')await api('/api/marks','POST',{student_id:s,subject_id:CUR,exam_type:$('et').value,marks:+m,max_marks:+$('mx').value});await api('/api/participation','POST',{subject_id:CUR,student_id:s,participation:+$('p'+s).value});alert('Saved ✔ (trigger logged marks)')}catch(e){err(e)}}
async function fasg(){const l=await api('/api/assignments');$('M').innerHTML=form('Create Assignment',[['subject_id','Subject',opt(FS,'subject_id','subject_name')],['title','Title','text'],['description','Description','text'],['deadline','Deadline','date']],'/api/assignments','fasg')+
`<div class="card"><h3>Assignments</h3>${tbl(['Subject','Title','Deadline',''],l.map(a=>[a.subject_name,a.title,a.deadline,`<button class="g" onclick="subs(${a.assignment_id})">Review submissions</button>`]))}</div><div id="sb"></div>`}
async function subs(id){const l=await api('/api/submissions/'+id);$('sb').innerHTML=`<div class="card"><h3>Submissions (marks out of 10)</h3><table>${l.map(s=>`<tr><td>${s.full_name}</td><td><a href="${s.file_url}" target="_blank">File</a></td><td><input id="g${s.submission_id}" type="number" value="${s.marks??''}"></td><td><button onclick="gs(${s.submission_id},${id})">Grade</button></td></tr>`).join('')||'<tr><td>No submissions</td></tr>'}</table></div>`}
async function gs(i,a){try{await api('/api/grade_sub','POST',{submission_id:i,marks:+$('g'+i).value});alert('Graded ✔')}catch(e){err(e)}}
// ---------- AI TOOLS ----------
async function ana(){const d=await api('/api/analytics');if(R=='student'){$('M').innerHTML=`<div class="card"><h3>📊 My performance analytics</h3>${chart(d.subjects.map(x=>x.name),[{name:'My marks %',c:'#1e40af',v:d.subjects.map(x=>x.marks)},{name:'Class average %',c:'#94a3b8',v:d.subjects.map(x=>x.class_avg)},{name:'My attendance %',c:'#16a34a',v:d.subjects.map(x=>x.att)}])}</div><div class="card"><h3>Exam trend</h3>${d.trend.map(x=>`<p>${x.exam_type} - ${x.avg}%${bar(x.avg)}</p>`).join('')}<p>🤖 Predicted final: <b>${d.stats.predicted}%</b></p></div>`;return}
$('M').innerHTML=`<div class="card"><h3>📊 Performance analytics</h3>${chart(d.subjects.map(x=>x.subject_name),[{name:'Average marks %',c:'#1e40af',v:d.subjects.map(x=>x.avg)},{name:'Pass %',c:'#7c3aed',v:d.subjects.map(x=>x.pass)},{name:'Avg attendance %',c:'#16a34a',v:d.subjects.map(x=>x.att)}])}</div><div class="card"><h3>Grade distribution</h3>${d.subjects.map(x=>distBar(x.subject_name,[x.b0,x.b1,x.b2,x.b3])).join('')}<p><small><span style="color:#ef4444">■</span> below 40 &nbsp; <span style="color:#f59e0b">■</span> 40-60 &nbsp; <span style="color:#3b82f6">■</span> 60-80 &nbsp; <span style="color:#16a34a">■</span> 80+</small></p></div>`}
async function attIns(){const l=await api('/api/att_insights');$('M').innerHTML=`<div class="card"><h3>🔔 Attendance Insights</h3><p style="color:var(--m)">Detects students below 75% attendance and sends them an alert notification (alerts are also sent automatically when attendance is saved).</p><button onclick="sendAl()">Send alerts to low-attendance students</button>${l.length?tbl(['Student','Subject','Present','Total','Attendance','Classes needed for 75%'],l.map(x=>[x.full_name,x.subject_name,x.present,x.total,tag(x.pct,75),x.need])):'<p>🎉 Everyone is above 75%</p>'}</div>`}
async function sendAl(){const d=await api('/api/alert_scan','POST',{});alert(d.sent+' new alert(s) sent')}
async function aiIns(){const d=await api('/api/ai_insights');$('M').innerHTML=`<div class="grid"><div class="stat"><span>Avg predicted %</span><h2>${d.avg_pred}%</h2></div><div class="stat"><span>At-risk students</span><h2>${d.at_risk.length}</h2></div></div><div class="card"><h3>🤖 AI Insights (overall)</h3>${d.insights.map(t=>`<p>💡 ${t}</p>`).join('')}</div><div class="card"><h3>⚠ At-risk students</h3>${tbl(['Name','Predicted','Attendance','Backlogs','Why'],d.at_risk.map(x=>[x.name,x.predicted+'%',x.att+'%',x.backlogs,x.why]))}</div><div class="card"><h3>🏆 Top predicted performers</h3>${d.top.map(x=>`<p>${x.name} - ${x.predicted}%</p>`).join('')}</div>`}
async function classAn(){const l=await api('/api/class_ai');$('M').innerHTML=l.map(s=>`<div class="card"><h3>${s.subject} (Sec ${s.section}) - ${s.students} students</h3><div class="grid">${[['Class average',s.avg+'%'],['Pass %',s.pass+'%'],['Avg attendance',s.att+'%'],['🤖 Expected average',s.pred_avg+'%']].map(x=>`<div class="stat"><span>${x[0]}</span><h2>${x[1]}</h2></div>`).join('')}</div>${distBar('Grade distribution (below 40 / 40-60 / 60-80 / 80+)',s.dist)}</div>`).join('')}
async function aiSug(){const l=await api('/api/class_ai');$('M').innerHTML=l.map(s=>`<div class="card"><h3>🧠 ${s.subject} - remedial focus</h3>${s.tips.map(t=>`<p>💡 ${t}</p>`).join('')}${s.weak.length?tbl(['Student','Why flagged','Suggested action'],s.weak.map(w=>[w.name,w.reasons.join('; '),w.action])):'<p>No students need remedial attention 🎉</p>'}</div>`).join('')}
async function plan(){const d=await api('/api/study_plan');$('M').innerHTML=`<div class="card"><h3>📅 Personalized Study Plan</h3><p>Weekly study budget: <b>${d.weekly} hours</b>. Time is split by how much each subject needs (weak marks and low attendance get more).</p>${tbl(['Subject','Marks','Attendance','Priority','Hours/week','Focus topics','Action'],d.subjects.map(s=>[s.name,s.m+'%',s.a+'%',`<span class="tag ${s.priority=='Low'?'ok':''}">${s.priority}</span>`,s.hours,s.topics.slice(0,3).join(', '),s.action]))}</div><div class="card"><h3>Weekly schedule</h3>${tbl(['Day','Sessions'],d.schedule.map(x=>[x.day,x.items.join('<br>')||'Rest / revision']))}</div><div class="card"><h3>💡 Tips</h3>${d.tips.map(t=>`<p>• ${t}</p>`).join('')}</div>`}
let CH=[];function chat(){if(!CH.length)CH=[['bot','Hi! I am your AI doubt solver. Ask me about DBMS, OS, networks, software engineering - or about your own attendance, marks, fees and exams.']];drawChat()}
function drawChat(){$('M').innerHTML=`<div class="card"><h3>💬 Doubt Solver (AI assistant)</h3><div id="cb" style="height:340px;overflow:auto;background:#f8fafc;border-radius:10px;padding:10px">${CH.map(m=>`<p style="text-align:${m[0]=='me'?'right':'left'}"><span style="display:inline-block;max-width:80%;padding:8px 12px;border-radius:12px;text-align:left;background:${m[0]=='me'?'#1e40af;color:#fff':'#e2e8f0'}">${m[1]}</span></p>`).join('')}</div><div class="row" style="grid-template-columns:1fr auto"><input id="dq" placeholder="Type your doubt, e.g. what is normalization?" onkeydown="if(event.key=='Enter')ask()"><button onclick="ask()">Ask</button></div></div>`;$('cb').scrollTop=1e5}
async function ask(){const q=$('dq').value.trim();if(!q)return;CH.push(['me',q.replace(/</g,'&lt;')]);try{const d=await api('/api/doubt','POST',{question:q});CH.push(['bot',d.answer])}catch(e){CH.push(['bot',e.message])}drawChat();$('dq').focus()}
async function career(){$('M').innerHTML=`<div class="card"><h3>🎯 Career Guidance</h3><p style="color:var(--m)">Suggests career paths from your subject performance, predicted marks, participation and interest.</p>Your interest<select id="ci" onchange="runC()"><option value="">No preference</option><option value="data">Data / Databases</option><option value="software">Software Development</option><option value="systems">Systems / Cloud / DevOps</option><option value="networking">Networking / Security</option><option value="ai">AI / Machine Learning</option><option value="research">Higher studies / Research</option></select><div id="cr"></div></div>`;runC()}
async function runC(){const d=await api('/api/career','POST',{interest:$('ci').value});$('cr').innerHTML=d.map((c,i)=>`<div class="card" style="border-left:5px solid var(--p)"><h3>${i+1}. ${c.title} <span class="tag ok">${c.fit}% fit</span></h3>${bar(c.fit)}<p>${c.why}</p><p><b>Build these skills:</b> ${c.skills.join(' · ')}</p></div>`).join('')}
// ---------- STUDENT ----------
const me=async()=>ME=await api('/api/me');
async function sdash(){await me();const s=ME.stats,ex=await api('/api/exams'),as=await api('/api/assignments');
$('M').innerHTML=`<div class="grid">${[['Attendance',s.att+'%'],['Current marks',s.marks+'%'],['Last exam',s.last+'%'],['Previous semester',s.prev+'%'],['Backlogs',s.backlogs],['Participation',s.part+'/10'],['Assignments',s.assign+'%'],['🤖 Predicted',s.predicted+'%']].map(x=>`<div class="stat"><span>${x[0]}</span><h2>${x[1]}</h2></div>`).join('')}</div>
<div class="card"><h3>📝 Upcoming exams</h3>${tbl(['Date','Subject','Exam'],ex.filter(e=>e.exam_date>=today).map(e=>[e.exam_date,e.subject_name,e.exam_type]))}</div>
<div class="card"><h3>📌 Pending assignments</h3>${tbl(['Subject','Title','Deadline'],as.filter(a=>!a.submission_id).map(a=>[a.subject_name,a.title,a.deadline]))}</div>`+await notifs()}
async function prof(){await me();const p=ME.profile;$('M').innerHTML=`<div class="card"><h3>👤 Profile</h3>${tbl(['Field','Value'],[['Name',p.full_name],['Email',p.email],['Enrollment',p.enrollment_number],['Course',p.course_name],['Department',p.department_name],['Semester',p.semester],['Section',p.section],['Previous semester %',p.prev_sem_percent],['Backlogs',p.backlogs],['Participation score',p.participation+'/10'],['Study hours/day',p.study_hours],['Admission date',p.admission_date],['Status',p.status]])}</div>`}
async function att(){await me();$('M').innerHTML=`<div class="card"><h3>Attendance (subject-wise) + insights</h3>${tbl(['Subject','Present','Total','Percentage','Insight'],ME.attendance.map(a=>{const n=a.total?Math.max(0,Math.ceil((0.75*a.total-a.present)/0.25)):0;return [a.subject_name,a.present,a.total,a.pct==null?'-':tag(a.pct,75),a.pct==null?'-':a.pct<75?`⚠ Attend the next <b>${n}</b> classes in a row to reach 75%`:`✔ You can skip up to ${Math.floor((a.present-0.75*a.total)/0.75)} class(es)`]}))}</div>`}
async function res(){await me();$('M').innerHTML=`<div class="card"><h3>Results</h3>${tbl(['Subject','Exam','Marks','Max','%'],ME.results.map(r=>[r.subject_name,r.exam_type,r.marks,r.max_marks,tag(r.pct,40)]))}</div>`}
async function sasg(){const l=await api('/api/assignments');$('M').innerHTML=form('Submit Assignment',[['assignment_id','Assignment',opt(l,'assignment_id',x=>x.subject_name+' - '+x.title)],['file','Upload file (PDF/doc/zip)','file'],['link','Or paste a link','text']],'/api/submit','sasg')+
`<div class="card"><h3>Assignments</h3>${tbl(['Subject','Title','Deadline','Status','Marks /10','My file'],l.map(a=>[a.subject_name,a.title,a.deadline,a.submission_id?'<span class="tag ok">Submitted</span>':'<span class="tag">Pending</span>',a.marks,a.file_url?`<a href="${a.file_url}" target="_blank">Open</a>`:'-']))}</div>`}
async function sleave(){const l=await api('/api/leave');$('M').innerHTML=form('Apply for Leave',[['from_date','From','date'],['to_date','To','date'],['reason','Reason','text']],'/api/leave','sleave')+`<div class="card"><h3>My Leave Requests</h3>${tbl(['From','To','Reason','Status'],l.map(x=>[x.from_date,x.to_date,x.reason,stag(x.status)]))}</div>`}
async function sfee(){const l=await api('/api/fees'),due=l.filter(f=>f.status!='paid').reduce((a,f)=>a+f.amount,0);$('M').innerHTML=`<div class="grid"><div class="stat"><span>Total due</span><h2>₹${due}</h2></div></div><div class="card"><h3>💳 Fee Details</h3>${tbl(['Description','Amount','Due date','Status','Paid on'],l.map(f=>[f.description,'₹'+f.amount,f.due_date,f.status=='paid'?'<span class="tag ok">Paid</span>':'<span class="tag">Pending</span>',f.payment_date]))}</div>`}
async function leaves(){const l=await api('/api/leave');$('M').innerHTML=`<div class="card"><h3>Leave Requests</h3>${tbl(['Student','From','To','Reason','Status',''],l.map(x=>[x.full_name,x.from_date,x.to_date,x.reason,stag(x.status),x.status=='pending'?`<button onclick="ld(${x.leave_id},'approved')">Approve</button><button class="r" onclick="ld(${x.leave_id},'rejected')">Reject</button>`:'']))}</div>`}
async function ld(i,s){await api('/api/leave_decide','POST',{leave_id:i,status:s});leaves()}
async function fees(){const l=await api('/api/fees');$('M').innerHTML=form('Add Fee',[['student_id','Student',opt(META.students,'student_id','label')],['description','Description','text'],['amount','Amount (₹)','number'],['due_date','Due date','date']],'/api/fees','fees')+`<div class="card"><h3>Fee Records</h3>${tbl(['Student','Description','Amount','Due','Status',''],l.map(f=>[f.full_name,f.description,'₹'+f.amount,f.due_date,f.status=='paid'?'<span class="tag ok">Paid</span>':'<span class="tag">Pending</span>',f.status=='paid'?'':`<button onclick="fp(${f.fee_id})">Mark paid</button>`]))}</div>`}
async function fp(i){await api('/api/fee_pay','POST',{fee_id:i});fees()}
async function exams(){const l=await api('/api/exams'),subs=R=='admin'?META.subjects:FS;$('M').innerHTML=(R!='student'?form('Schedule Exam',[['subject_id','Subject',opt(subs,'subject_id','subject_name')],['exam_type','Exam type','<option>Internal</option><option>Midterm</option><option>Final</option>'],['exam_date','Date','date'],['max_marks','Max marks','number']],'/api/exams','exams'):'')+`<div class="card"><h3>📝 Exam Schedule</h3>${tbl(['Date','Subject','Exam','Max marks'],l.map(e=>[e.exam_date,e.subject_name,e.exam_type,e.max_marks]))}</div>`}
async function fprof(){const f=(await api('/api/fprofile'))[0];$('M').innerHTML=`<div class="card"><h3>👤 Faculty Profile</h3>${tbl(['Field','Value'],[['Name',f.full_name],['Email',f.email],['Employee code',f.employee_code],['Department',f.department_name],['Designation',f.designation],['Qualification',f.qualification],['Experience',f.experience_years+' years'],['Specialization',f.specialization],['Joining date',f.joining_date],['Status',f.status]])}</div>`}
</script></body></html>'''

if __name__ == "__main__":
    init(); port = int(os.environ.get("PORT", 8000)); print(f"Running on http://localhost:{port}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
