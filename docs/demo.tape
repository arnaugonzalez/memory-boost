# Renders docs/demo.gif. Reproduce from the repo root:
#   scripts/render-demo.sh
Output docs/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1280
Set Height 760
Set Padding 24
Set Theme "GitHub Dark"
Set TypingSpeed 30ms

Hide
Type "unset MEMORY_BOOST_HOME MEMORY_BOOST_CHECKPOINTS MEMORY_BOOST_WIKI MEMORY_BOOST_INDEX XDG_DATA_HOME XDG_CACHE_HOME; export PATH=/vhs/.demo/bin:/usr/bin:/bin HOME=/tmp/h PS1='$ ' && cd ~ && memory-boost init --example >/dev/null && clear" Enter
Sleep 3s
Show

Type "# Back on acme-api after two months. The agent's memory says what we decided." Enter
Sleep 1.2s
Type "# Is any of it still true?" Enter
Sleep 1s
Type "memory-boost drift" Sleep 300ms Enter
Sleep 6s
Type "clear" Enter
Type "# The Postgres pin expired in June. What was it for?" Enter
Sleep 1s
Type "memory-boost page pin-postgres-15" Sleep 300ms Enter
Sleep 4.5s
Type "clear" Enter
Type "# pixel-notes hit a retry bug last month. Does acme-api need that lesson?" Enter
Sleep 1s
Type "memory-boost lessons --project acme-api" Sleep 300ms Enter
Sleep 3s
Type "# Expired decisions flagged, a lesson carried over: the agent starts from what is still true."
Sleep 6s
