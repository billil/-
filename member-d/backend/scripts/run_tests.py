"""可重复生成测试文本与 JSON 报告。"""
import json,unittest,time,sys,platform,os
from pathlib import Path

def main():
    root=Path(__file__).resolve().parents[1];os.chdir(root);sys.path.insert(0,str(root))
    class Report(unittest.TextTestResult):
        def __init__(self,*a,**kw):super().__init__(*a,**kw);self.records=[]
        def addSuccess(self,t):super().addSuccess(t);self.records.append({'test':t.id(),'status':'passed'})
        def addFailure(self,t,e):super().addFailure(t,e);self.records.append({'test':t.id(),'status':'failed'})
        def addError(self,t,e):super().addError(t,e);self.records.append({'test':t.id(),'status':'error'})
    start=time.monotonic()
    with (root/'docs/test-output.txt').open('w',encoding='utf-8') as f:
        result=unittest.TextTestRunner(stream=f,verbosity=2,resultclass=Report).run(unittest.defaultTestLoader.discover('tests'))
    report={'label':'后端自动化验证；模型测试使用替身或本地HTTP合成服务，不是真实AI效果评测','python':platform.python_version(),'platform':platform.system(),'count':result.testsRun,'passed':sum(x['status']=='passed' for x in result.records),'failures':len(result.failures),'errors':len(result.errors),'duration_seconds':round(time.monotonic()-start,3),'tests':result.records}
    (root/'docs/test-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"{report['passed']}/{report['count']} tests passed. See docs/test-report.json")
    return 0 if result.wasSuccessful() else 1
if __name__=='__main__':raise SystemExit(main())
