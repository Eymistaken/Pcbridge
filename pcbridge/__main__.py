"""`python -m pcbridge ...` is the same as the `pcbridge` command."""

from .cli.main import main

raise SystemExit(main())
