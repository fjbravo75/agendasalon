$ErrorActionPreference = "Stop"

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class AgendaSalonCredentialWriter
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

    [DllImport("Advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool CredWrite(ref Credential credential, UInt32 flags);
}
"@

function Save-AgendaSalonCredential {
    param(
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$UserName,
        [Parameter(Mandatory = $true)][SecureString]$Secret
    )

    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    $blobPointer = [IntPtr]::Zero
    $bytes = $null
    $plain = $null

    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        if ([string]::IsNullOrWhiteSpace($plain)) {
            throw "La credencial $Target no puede estar vacia."
        }
        $bytes = [Text.Encoding]::UTF8.GetBytes($plain)
        $blobPointer = [Runtime.InteropServices.Marshal]::AllocCoTaskMem($bytes.Length)
        [Runtime.InteropServices.Marshal]::Copy($bytes, 0, $blobPointer, $bytes.Length)

        $credential = New-Object AgendaSalonCredentialWriter+Credential
        $credential.Type = 1
        $credential.TargetName = $Target
        $credential.Comment = "AgendaSalon - configuracion Brevo"
        $credential.CredentialBlobSize = $bytes.Length
        $credential.CredentialBlob = $blobPointer
        $credential.Persist = 2
        $credential.UserName = $UserName

        if (-not [AgendaSalonCredentialWriter]::CredWrite([ref]$credential, 0)) {
            $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            throw "Windows Credential Manager devolvio el error $errorCode."
        }
    }
    finally {
        if ($bytes) {
            [Array]::Clear($bytes, 0, $bytes.Length)
        }
        if ($blobPointer -ne [IntPtr]::Zero) {
            if ($bytes) {
                [Runtime.InteropServices.Marshal]::Copy($bytes, 0, $blobPointer, $bytes.Length)
            }
            [Runtime.InteropServices.Marshal]::FreeCoTaskMem($blobPointer)
        }
        if ($bstr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
        $plain = $null
    }
}

$exitCode = 0
try {
    Write-Host "AgendaSalon - custodia segura de credenciales Brevo" -ForegroundColor Cyan
    Write-Host "Las claves no se mostraran ni se guardaran en el proyecto."

    $smtpKey = Read-Host "Pega la clave SMTP de Brevo" -AsSecureString
    $apiKey = Read-Host "Pega la clave API v3 de Brevo" -AsSecureString

    Save-AgendaSalonCredential `
        -Target "AgendaSalonBrevoSmtpKey" `
        -UserName "b204f9001@smtp-brevo.com" `
        -Secret $smtpKey
    Save-AgendaSalonCredential `
        -Target "AgendaSalonBrevoApiKey" `
        -UserName "brevo-api-v3" `
        -Secret $apiKey

    Write-Host "Las dos credenciales se han guardado correctamente." -ForegroundColor Green
}
catch {
    $exitCode = 1
    Write-Host "No se han podido guardar las credenciales." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
}
finally {
    $smtpKey = $null
    $apiKey = $null
}

Read-Host "Pulsa Intro para cerrar esta ventana"
exit $exitCode
