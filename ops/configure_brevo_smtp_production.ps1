[CmdletBinding()]
param(
    [string]$SshTarget = "root@164.90.182.38",
    [string]$IdentityFile = "/home/fjbravo75/.ssh/id_ed25519"
)

$ErrorActionPreference = "Stop"

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class AgendaSalonBrevoCredentialReader
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct Credential
    {
        public UInt32 Flags;
        public UInt32 Type;
        public string TargetName;
        public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public UInt32 CredentialBlobSize;
        public IntPtr CredentialBlob;
        public UInt32 Persist;
        public UInt32 AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }

    [DllImport("Advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool CredRead(string target, UInt32 type, UInt32 flags, out IntPtr credential);

    [DllImport("Advapi32.dll")]
    public static extern void CredFree(IntPtr buffer);
}
"@

function Get-AgendaSalonSecret {
    param([Parameter(Mandatory = $true)][string]$Target)

    $credentialPointer = [IntPtr]::Zero
    if (-not [AgendaSalonBrevoCredentialReader]::CredRead(
        $Target,
        1,
        0,
        [ref]$credentialPointer
    )) {
        throw "No se encontro $Target en Windows Credential Manager."
    }

    try {
        $credential = [Runtime.InteropServices.Marshal]::PtrToStructure(
            $credentialPointer,
            [type][AgendaSalonBrevoCredentialReader+Credential]
        )
        $bytes = New-Object byte[] $credential.CredentialBlobSize
        [Runtime.InteropServices.Marshal]::Copy(
            $credential.CredentialBlob,
            $bytes,
            0,
            $bytes.Length
        )
        try {
            return [Text.Encoding]::UTF8.GetString($bytes)
        }
        finally {
            [Array]::Clear($bytes, 0, $bytes.Length)
        }
    }
    finally {
        [AgendaSalonBrevoCredentialReader]::CredFree($credentialPointer)
    }
}

$smtpKey = Get-AgendaSalonSecret -Target "AgendaSalonBrevoSmtpKey"
try {
    $payload = @{ password = $smtpKey } | ConvertTo-Json -Compress
    $remotePython = @'
import json
import os
import shutil
import smtplib
import ssl
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ENV_PATH = Path("/etc/agendasalon/agendasalon.env")
SMTP_HOST = "smtp-relay.brevo.com"
SMTP_PORT = 2525
SMTP_USER = "b204f9001@smtp-brevo.com"


def quote_environment_value(value):
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'"{escaped}"'


def update_environment_file(values):
    original = ENV_PATH.read_text(encoding="utf-8")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = ENV_PATH.with_name(f"{ENV_PATH.name}.before-brevo-{timestamp}")
    shutil.copy2(ENV_PATH, backup)
    os.chmod(backup, stat.S_IRUSR | stat.S_IWUSR)

    pending = dict(values)
    updated = []
    seen = set()
    for line in original.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in pending:
                if key not in seen:
                    updated.append(f"{key}={quote_environment_value(pending[key])}")
                    seen.add(key)
                continue
        updated.append(line)

    if updated and updated[-1] != "":
        updated.append("")
    for key, value in pending.items():
        if key not in seen:
            updated.append(f"{key}={quote_environment_value(value)}")

    fd, temporary_name = tempfile.mkstemp(prefix="agendasalon.env.", dir=ENV_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(updated).rstrip("\n") + "\n")
        os.chmod(temporary_name, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary_name, ENV_PATH)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


payload = json.load(sys.stdin)
password = payload.get("password", "")
if not password:
    raise SystemExit("No se recibio una clave SMTP valida.")

context = ssl.create_default_context()
with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
    smtp.ehlo()
    smtp.starttls(context=context)
    smtp.ehlo()
    smtp.login(SMTP_USER, password)

update_environment_file(
    {
        "AGENDA_TRANSACTIONAL_EMAIL_ENABLED": "1",
        "EMAIL_HOST": SMTP_HOST,
        "EMAIL_PORT": str(SMTP_PORT),
        "EMAIL_HOST_USER": SMTP_USER,
        "EMAIL_HOST_PASSWORD": password,
        "DEFAULT_FROM_EMAIL": "AgendaSalon <agendasalon@brvsoftwarestudio.com>",
        "EMAIL_USE_TLS": "1",
        "EMAIL_USE_SSL": "0",
    }
)

print("BREVO_SMTP_AUTH_AND_CONFIG_OK")
'@

    $scriptBytes = [Text.Encoding]::UTF8.GetBytes($remotePython)
    $scriptHex = -join ($scriptBytes | ForEach-Object { $_.ToString("x2") })
    $remoteCommand = (
        "python3 -c 'exec((0x{0}).to_bytes({1}))'" -f
        $scriptHex,
        $scriptBytes.Length
    )
    $sshArguments = @(
        "-e",
        "ssh",
        "-i", $IdentityFile,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        $SshTarget,
        $remoteCommand
    )

    $payload | & wsl.exe $sshArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Brevo no acepto la clave SMTP o no se pudo guardar la configuracion."
    }
}
finally {
    $smtpKey = $null
    $payload = $null
}
