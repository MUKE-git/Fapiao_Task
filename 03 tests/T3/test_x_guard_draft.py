"""研发固定样本、待测试工程师复核和用户审核；不调用真实模型。"""
import os,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,r'C:\Users\lenovo\Desktop\AI_Player\fapiao3.0')
from pipeline import _consolidate_confirmed_same_invoice, MessageOutcome
from data_model import MailRecord, FileRecord, InvoiceRecord, InvoiceFields, SourceMeta, MatchedItinerary, STATUS_MISSING_VOUCHER
from config import RunConfig, ensure_output_dirs

class Test(unittest.TestCase):
 def test_guards(self):
  with tempfile.TemporaryDirectory() as tmp:
   cfg=RunConfig('h','u','p',output_root=tmp);ensure_output_dirs(cfg)
   def item(mid,kind,seq,trip,skip):
    files=[FileRecord(file_seq='01',file_type=kind,internal_name=f'{mid}_01_file.{"png" if kind=="图片" else "xml"}'),FileRecord(file_seq='02',file_type='PDF',internal_name=f'{mid}_02_trip.pdf')]
    rec=InvoiceRecord(record_id=f'{mid}-01',fields=InvoiceFields(invoice_number='1'*20,travel_time='2026-03-23 09:02',origin_dest='A→B'),source=SourceMeta(source_file_seq=['01']),status=STATUS_MISSING_VOUCHER)
    if trip: rec.matched_itinerary=MatchedItinerary('02','10')
    mail=MailRecord(mail_id=mid,files=files,records=[rec]);mail.excel_skip_record_ids={rec.record_id} if skip else set()
    return MessageOutcome(mail)
   a=item('Msg1','XML','01',False,False); b=item('Msg2','图片','01',True,True); c=item('Msg3','图片','01',True,True)
   for o in (b,c): Path(cfg.extract_dir,o.mail.files[1].internal_name).write_bytes(b'same')
   a.alerts.append({'flags':['Msg1-01 缺凭证，需回原邮件核对']})
   _consolidate_confirmed_same_invoice([a,b,c],cfg)
   self.assertTrue(any('缺凭证' in str(x) for x in a.alerts))
   self.assertEqual(a.mail.records[0].status,STATUS_MISSING_VOUCHER)
   self.assertEqual(a.mail.records[0].matched_itinerary.mail_id,'Msg2')
   a.mail.records[0].matched_itinerary=None
   Path(cfg.extract_dir,c.mail.files[1].internal_name).write_bytes(b'other')
   _consolidate_confirmed_same_invoice([a,b,c],cfg)
   self.assertIsNone(a.mail.records[0].matched_itinerary)
   self.assertTrue(any('多份行程单' in str(x) for x in a.alerts))
if __name__=='__main__': unittest.main()
