@echo off
REM Expose the WSL-hosted KRY notary (0.0.0.0:7047 inside WSL) to the LAN so the
REM prover (on another machine) can reach it via NOTARY_ADDR=<this-host-LAN-IP>:7047.
REM
REM WSL binds inside the WSL VM; Windows must port-proxy the host port to the WSL IP
REM (which changes per boot, so we resolve it live). Run on the NOTARY's Windows host.
REM Per KRY_T2_FINDINGS_REPORT.md §7a this worked non-elevated last session; if netsh
REM refuses, run this from an elevated prompt.
REM
REM   expose_notary_lan.cmd          add the portproxy + firewall rule
REM   expose_notary_lan.cmd remove   tear it down
SETLOCAL
SET PORT=7047

IF "%1"=="remove" (
  netsh interface portproxy delete v4tov4 listenport=%PORT% listenaddress=0.0.0.0
  netsh advfirewall firewall delete rule name="KRY Notary %PORT%"
  echo Removed portproxy + firewall rule for %PORT%.
  GOTO :eof
)

REM resolve the current WSL IP (eth0)
FOR /F "tokens=*" %%i IN ('wsl bash -lc "hostname -I | awk '{print $1}'"') DO SET WSLIP=%%i
echo WSL IP: %WSLIP%
netsh interface portproxy add v4tov4 listenport=%PORT% listenaddress=0.0.0.0 connectport=%PORT% connectaddress=%WSLIP%
netsh advfirewall firewall add rule name="KRY Notary %PORT%" dir=in action=allow protocol=TCP localport=%PORT%
echo.
echo Notary now reachable on the LAN at THIS host's IP, port %PORT%.
echo On the prover:  set NOTARY_ADDR=<this-host-LAN-IP>:%PORT%  then run attestation_prove.
echo On the verifier: kry_tlsn_verify ... --notary-key ^<the notary public key^>
ENDLOCAL
