"""研发制作，待用户审核；固定文件及预期，非正式验收。"""
import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook
from config import RunConfig, ensure_output_dirs
from delivery_exporter import prepare_delivery

class GroupDelivery(unittest.TestCase):
    def test_cross_email_pdf_and_trip_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=RunConfig("host","user","pw",output_root=tmp); ensure_output_dirs(cfg)
            Workbook().save(cfg.summary_excel_file)
            for name in ("invoice.pdf","trip1.pdf","trip2.pdf","trip3.pdf"):
                Path(cfg.extract_dir,name).write_bytes(b"%PDF-1.4\n" + (b"invoice" if name=="invoice.pdf" else b"same trip"))
            no="12345678901234567890"; other="22345678901234567890"
            def tripmail(mid, name, number, duplicate=False):
                return {"mail_id":mid,"mail_uid":mid,"files":[{"file_seq":"01","file_type":"PDF","internal_name":"invoice.pdf"},{"file_seq":"02","file_type":"PDF","internal_name":name}],"records":[{"record_id":mid+"-01","fields":{"invoice_number":number,"total_amount":"10","receipt_type":"服务"},"source":{"source_file_seq":["01"],"delivery_blocked":duplicate,"same_invoice_duplicate":duplicate},"matched_itinerary":{"file_seq":"02","match_amount":"10"}}],"itineraries":[{"record_id":mid+"-02","source_file_seq":["02"],"match_amount":"10"}]}
            main={"mail_id":"M1","mail_uid":"1","files":[{"file_seq":"01","file_type":"XML","internal_name":"a.xml"}],"records":[{"record_id":"M1-01","fields":{"invoice_number":no,"total_amount":"10","receipt_type":"服务"},"source":{"source_file_seq":["01"],"same_invoice_records":[{"mail_id":"M1","record_id":"M1-01"},{"mail_id":"M2","record_id":"M2-01"},{"mail_id":"M3","record_id":"M3-01"}]}}],"itineraries":[]}
            a=tripmail("M2","trip1.pdf",no,True); b=tripmail("M3","trip2.pdf",no,True); c=tripmail("M4","trip3.pdf",other)
            plan=prepare_delivery(cfg,[main,a,b,c]); names=[e.path.name for e in plan.entries]
            self.assertIn("invoice.pdf",names)
            self.assertIn("trip1.pdf",names)
            self.assertNotIn("trip2.pdf",names)
            self.assertIn("trip3.pdf",names)
            self.assertEqual(len(plan.mails[2]["files"]),2)
            self.assertFalse(any("归属未决" in x for x in plan.issues))

if __name__=="__main__": unittest.main()
