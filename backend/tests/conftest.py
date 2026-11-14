import os
import tempfile

# Testler gerçek oturum dosyasını (backend/data/session.pkl) asla okumasın / ezmesin
os.environ["SESSION_FILE"] = os.path.join(tempfile.mkdtemp(), "session.pkl")
os.environ["AUDIT_FILE"] = os.path.join(tempfile.mkdtemp(), "audit.jsonl")
os.environ["MACHINES_DIR"] = tempfile.mkdtemp()  # kullanıcı makineleri ("Makinelerim") geçici klasörde
