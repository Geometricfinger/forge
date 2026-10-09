#!/usr/bin/env python3
from research_runner.runner import main
from research_runner.core import Blocked,canonical
if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as ex:
        print(canonical({'status':'BLOCKED','reason':str(ex) if isinstance(ex,Blocked) else type(ex).__name__}).decode())
        raise SystemExit(2)
