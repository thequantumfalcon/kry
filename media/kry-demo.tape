# KRY live demo — render with:  vhs media/kry-demo.tape
# Large font + full-height terminal so the README GIF stays readable on GitHub.
Output media/kry-demo.gif
Output media/kry-demo.mp4
Set Shell "bash"
Set FontSize 18
Set Width 1500
Set Height 1000
Set Padding 26
Set Theme "Catppuccin Mocha"
Set TypingSpeed 30ms
Hide
Type "export KRY_DEMO_PACE=1.4 TERM=xterm-256color"
Enter
Show
Sleep 600ms
Type "bash examples/demo.sh"
Sleep 500ms
Enter
Sleep 18s
