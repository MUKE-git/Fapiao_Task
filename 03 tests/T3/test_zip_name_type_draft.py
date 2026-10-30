"""研发制作、待用户审核；本地固定样本，不访问邮箱或模型服务。"""
import io
import os
import tempfile
import unittest
import zipfile
from email.message import EmailMessage
from attachment_processor import collect_mail_files, _zip_entry_name
from config import RunConfig, ensure_output_dirs
from data_model import MailRecord
from type_classifier import collect_classification_mismatches

def archive_gbk(name):
    raw = name.encode("gbk")
    placeholder = "a" * len(raw)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(placeholder, b"%PDF-1.4 fake")
    return out.getvalue().replace(placeholder.encode(), raw)

class Checks(unittest.TestCase):
    def test_real_zip_collection_recovers_gbk(self):
        name = "阳光出行-行程单.pdf"
        msg = EmailMessage()
        msg.set_content("test")
        msg.add_attachment(archive_gbk(name), maintype="application", subtype="zip", filename="a.zip")
        with tempfile.TemporaryDirectory() as tmp:
            cfg = RunConfig("host", "user", "pw", output_root=tmp)
            ensure_output_dirs(cfg)
            mail = MailRecord(mail_id="Msg1")
            collect_mail_files(msg, mail, cfg)
            self.assertEqual(mail.files[0].original_name, name)
            self.assertEqual(mail.files[0].file_type, "PDF")
    def test_utf8_and_legitimate_accented_names_preserved(self):
        for name in ("阳光行程单.pdf", "résumé.pdf", "plain.pdf"):
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as z: z.writestr(name, b"x")
            with zipfile.ZipFile(io.BytesIO(out.getvalue())) as z:
                self.assertEqual(_zip_entry_name(z.infolist()[0]), name)
    def test_only_opposite_known_roles_conflict(self):
        for local, ai, expected in [("Unknown", "Itinerary", False), ("Invoice", "Unknown", False), ("Itinerary", "Itinerary", False), ("Invoice", "Itinerary", True), ("Itinerary", "Invoice", True)]:
            task = {"files": [{"local_path": "test.pdf", "identified_type": local}]}
            data = {"file_classifications": [{"file": "test.pdf", "role": ai}]}
            with self.subTest(local=local, ai=ai):
                self.assertEqual(bool(collect_classification_mismatches(task, data, "")), expected)

if __name__ == "__main__": unittest.main()
