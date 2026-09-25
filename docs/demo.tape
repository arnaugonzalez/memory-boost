# Renders docs/demo.gif. Reproduce from the repo root:
#   scripts/render-demo.sh
Output docs/demo.gif

Set Shell "bash"
Set FontSize 18
Set Width 1280
Set Height 720
Set Padding 28
Set Theme "Catppuccin Mocha"
Set TypingSpeed 22ms

Hide
Type "export PATH=/vhs/.demo/bin:$PATH HOME=/tmp/h MEMORY_BOOST_TRANSCRIPTS=/tmp/h/.local/share/memory-boost/example_transcripts PS1='\[\e[1;35m\]$\[\e[0m\] ' && memory-boost init --example >/dev/null && clear" Enter
Sleep 3s
Show

Type "# what in my agents' memory went stale?" Enter
Type "memory-boost drift" Sleep 300ms Enter
Sleep 5s
Type "clear" Enter
Type "# how do my sessions with them actually go?" Enter
Type "memory-boost mine" Sleep 300ms Enter
Sleep 4.5s
Type "clear" Enter
Type "# did another project already learn something I need?" Enter
Type "memory-boost lessons --project acme-api" Sleep 300ms Enter
Sleep 3.5s
