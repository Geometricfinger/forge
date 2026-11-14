#!/usr/bin/env python3
"""Ask and inspect a completed mission, without new intake or source execution."""
from research_runner.supervise import main
from research_runner.core import Blocked,canonical
if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as ex:
        print(canonical({'state':'BLOCKED','reason':str(ex) if isinstance(ex,Blocked) else type(ex).__name__,
                         'release_approved':False,'model_calls':0}).decode())
        raise SystemExit(2)
