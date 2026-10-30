"""测试工程师制作、待用户审核；固定邮箱与 PDF 结果，真实 XML、主流程、Excel、ZIP。"""
import sys, io, json, zipfile, tempfile, unittest
from pathlib import Path
from email.message import EmailMessage
from unittest.mock import patch
sys.path.insert(0, r'C:\Users\lenovo\Desktop\AI_Player\fapiao3.0')
from config import RunConfig
from pipeline import run_pipeline
ROOT=Path(r'C:\Users\lenovo\Downloads')
XML=ROOT/'T3出行电子发票(XML)A.xml'
PDF=ROOT/'T3出行电子发票A (1).pdf'
NO='26447000000342589935'
def msg(files):
 m=EmailMessage();m['Subject']='发票 测试工程师制作';m['Date']='Mon, 27 Apr 2026 09:00:00 +0800';m.set_content('test')
 for name,content in files:m.add_attachment(content,maintype='application',subtype='octet-stream',filename=name)
 return m
class Fake:
 def __init__(self,ms):self.ms=ms
 def login(self,*a):return 'OK',[]
 def select(self,*a):return 'OK',[b'2']
 def status(self,*a):return 'OK',[b'INBOX (MESSAGES 2 UNSEEN 2)']
 def logout(self):return 'BYE',[]
 def uid(self,cmd,*args):
  if cmd=='SEARCH':return 'OK',[b'101 102']
  return 'OK',[(b'payload',self.ms[int(args[0])-101].as_bytes())]
def fixed(item,cfg,subject,date,run_id):
 from processor_contracts import DocumentResult
 if '行程' in item.meta.original_name:
  return DocumentResult(item.meta.file_seq,'Itinerary',{'match_amount':'121.74','travel_time':'2026-04-27 12:00','origin_dest':'甲→乙'},[item.meta.file_seq],error_reason='FAKE_REAL_FAILURE')
 return DocumentResult(item.meta.file_seq,'Invoice',{'invoice_number':NO,'invoice_date':'2026-04-27','total_amount':'121.74','receipt_type':'客运服务费','seller':'南京领行科技股份有限公司广州分公司'},[item.meta.file_seq])
class Test(unittest.TestCase):
 def test_multiple_trips_identical_or_conflicting(self):
  from data_model import MailRecord,FileRecord,InvoiceRecord,InvoiceFields,SourceMeta,MatchedItinerary
  from config import ensure_output_dirs
  from pipeline import MessageOutcome,_consolidate_confirmed_same_invoice
  for same in (True,False):
   with self.subTest(same=same),tempfile.TemporaryDirectory() as tmp:
    cfg=RunConfig('host','u','p',output_root=tmp);ensure_output_dirs(cfg);out=[]
    for i in (1,2,3):
     mid=f'Msg{i}'; name=f'{mid}_01_trip.pdf';Path(cfg.extract_dir,name).write_bytes(b'%PDF-same' if same or i!=3 else b'%PDF-other')
     m=MailRecord(mail_id=mid,files=[FileRecord('01','PDF',internal_name=name)])
     m.records=[InvoiceRecord(f'{mid}-01',fields=InvoiceFields(invoice_number=NO,travel_time='2026-04-27 12:00',origin_dest='甲→乙'),source=SourceMeta(['01']))]
     if i>1:m.excel_skip_record_ids.add(f'{mid}-01');m.records[0].matched_itinerary=MatchedItinerary('01','121.74')
     out.append(MessageOutcome(m))
    _consolidate_confirmed_same_invoice(out,cfg)
    if same:self.assertEqual(out[0].mail.records[0].matched_itinerary.mail_id,'Msg2')
    else:self.assertIsNone(out[0].mail.records[0].matched_itinerary);self.assertTrue(any('人工核对' in str(x) for x in out[0].alerts))
 def test_bad_image_not_satisfy_voucher(self):
  from data_model import MailRecord,FileRecord,InvoiceRecord,InvoiceFields,SourceMeta
  from pipeline import MessageOutcome,_consolidate_confirmed_same_invoice
  with tempfile.TemporaryDirectory() as tmp:
   cfg=RunConfig('host','u','p',output_root=tmp)
   a=MailRecord(mail_id='Msg1',files=[FileRecord('01','XML')]);b=MailRecord(mail_id='Msg2',files=[FileRecord('01','图片')])
   a.records=[InvoiceRecord('Msg1-01',fields=InvoiceFields(invoice_number=NO),source=SourceMeta(['01']),status='缺凭证')]
   b.records=[InvoiceRecord('Msg2-01',fields=InvoiceFields(invoice_number=NO),source=SourceMeta(['01']))];b.excel_skip_record_ids.add('Msg2-01')
   outcome=MessageOutcome(a,alerts=[{'flags':['Msg1-01 缺凭证']}]);_consolidate_confirmed_same_invoice([outcome,MessageOutcome(b)],cfg)
   self.assertEqual(a.records[0].status,'缺凭证');self.assertEqual(len(outcome.alerts),1)
 def test_whole(self):
  files1=[('票.xml',XML.read_bytes())]; files2=[('发票.pdf',PDF.read_bytes()),('行程单.pdf',PDF.read_bytes()+b'trip')]
  with tempfile.TemporaryDirectory() as tmp:
   cfg=RunConfig('host','u','p',output_root=tmp,dashscope_api_key='fixed-no-network')
   with patch('pipeline.imaplib.IMAP4_SSL',return_value=Fake([msg(files1),msg(files2)])),patch('pipeline.process_pdf',side_effect=fixed): _,stats=run_pipeline(cfg)
   self.assertIsNone(stats['run_error']);self.assertEqual(stats['delivery_status'],'ready');self.assertEqual(stats['summary_row_count'],1)
   flags=str(stats['not_found_alerts']);self.assertIn('FAKE_REAL_FAILURE',flags)
   with zipfile.ZipFile(io.BytesIO(stats['delivery_zip_bytes'])) as z:
    self.assertEqual(len([x for x in z.namelist() if x.endswith('.pdf')]),2);data=json.loads(z.read('Invoice_Task/task_debug.json'));self.assertEqual(data[0]['records'][0]['matched_itinerary']['mail_id'],'Msg2')
if __name__=='__main__':unittest.main()
