"""Explicit eight-stock public collection; no inference or production mutation."""
import concurrent.futures as cf
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
from mt1.ta_evidence import search, fetch
from mt1.ta_research import save, now, digest

QUERIES = {
 '001309.SZ': '德明利 存储 价格 毛利 库存',
 '603986.SH': '兆易创新 利基 DRAM 涨价 持续',
 '688195.SH': '腾景科技 光通信 客户 订单 交付',
 '301511.SZ': '德福科技 高端 铜箔 加工费 量产',
 '601138.SH': '工业富联 AI 服务器 现金流 交付',
 '01810.HK': '小米集团 研报 汽车 成本 毛利率',
 '03690.HK': '美团 研报 补贴 京东 竞争',
 '00700.HK': '腾讯 研报 AI 广告 游戏 投入',
}
URLS = {
 'memory': 'https://www.trendforce.cn/presscenter/news/20260703-13133.html',
 'niche': 'https://www.trendforce.com.tw/presscenter/news/20260616-13100.html',
 'xiaomi_broker': 'https://m.hibor.com.cn/wap_detail.aspx?id=8d55abb75e3a4fa618888f32dc1c93f0',
 'xiaomi_broker_alt': 'https://www.gelonghui.com/news/5302705',
 'tencent_broker': 'https://finance.sina.com.cn/stock/relnews/hk/2026-08-18/doc-inintatv7366336.shtml',
 'jd_peer': 'https://ir.jd.com/news-releases/news-release-details/jdcom-announces-second-quarter-and-interim-2026-results',
 'jd_peer_pdf': 'https://ir.jd.com/static-files/2f8b0fa8-16a5-4d19-a07c-ee6d656223e8',
 'fii_report': 'https://wap.stockstar.com/detail/SN2026081100031131',
}
def one(item):
 code, query = item
 p=ROOT/'search'/(code+'.json')
 if p.exists():return json.loads(p.read_text())
 task={'task_id':'closeout-'+code, 'code':code, 'query':query,'topic':'proposition',
       'published_after':'2026-08-01','published_before':'2026-09-16'}
 result=search(task,ROOT/'search',('baidu','eastmoney_news','bing_rss'),max_hits=3)
 result['bodies']=[{'lead':h,'receipt':fetch(h['url'],ROOT/'bodies'/digest(h['url'])[:20],timeout=12)} for h in result['hits'][:2]]
 save(p,result);return result
if __name__=='__main__':
 with cf.ThreadPoolExecutor(max_workers=3) as pool:
  searches=list(pool.map(one, QUERIES.items()))
 with cf.ThreadPoolExecutor(max_workers=3) as pool:
  receipts=dict(zip(URLS,pool.map(lambda k:fetch(URLS[k],ROOT/'targeted'/k,timeout=12),URLS)))
 save(ROOT/'bounded-receipt.json',{'as_of':now(),'queries':searches,'targeted':receipts,'model_calls':0,
      'limits':{'queries_per_stock':1,'max_hits':3,'max_bodies_per_stock':2,'targeted_urls':len(URLS),'concurrency':3}})
 print('completed',flush=True)
