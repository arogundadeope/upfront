import sys; sys.path.insert(0, 'src')
from fetch import fetch_domain, log
plan = [
  ("ucsfhealth.org", None),
  ("sutterhealth.org", ["PACIFIC MEDICAL","MILLS PENINSULA","EDEN","NOVATO","ALTA BATES","DELTA","SOLANO","SANTA ROSA"]),
  ("johnmuirhealth.com", ["Walnut Creek","Concord"]),
  ("elcaminohealth.org", None),
  ("whhs.com", None),
  ("goodsamsanjose.com", None),
  ("mymarinhealth.org", None),
  ("cchealth.org", None),
  ("smchealth.org", None),
  ("strosehospital.org", None),
  ("northbay.org", None),
  ("sonomavalleyhospital.org", None),
  ("commonspirit.org", ["Sequoia"]),
  ("providence.org", ["Queen of the Valley","Santa Rosa Memorial","Petaluma","Healdsburg"]),
  ("adventisthealth.org", ["HELENA"]),
  ("ahmchealth.com", ["Seton"]),
  ("ucsfbenioffchildrens.org", ["Oakland"]),
  ("stanfordchildrens.org", None),
  ("zuckerbergsanfranciscogeneral.org", None),
  ("stanfordhealthcare.org", None),
  ("chinesehospital-sf.org", None),
]
for domain, keep in plan:
    print("==", domain, flush=True)
    try:
        log(fetch_domain(domain, keep))
    except Exception as e:
        print("FAILED", domain, e, flush=True)
print("ALL DONE", flush=True)
