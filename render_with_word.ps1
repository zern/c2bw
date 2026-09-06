$ErrorActionPreference = 'Stop'

$inputPath = (Get-ChildItem -LiteralPath 'D:\pythonCode\c2bw' -Filter '*.docx' | Select-Object -First 1).FullName
$outputDir = 'D:\pythonCode\c2bw\qa_withdrawal_word'
$pdfPath = Join-Path $outputDir 'withdrawal_application.pdf'

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open($inputPath, $false, $true)
    $document.Repaginate()
    $pageCount = $document.ComputeStatistics(2)
    $document.ExportAsFixedFormat($pdfPath, 17)
    Write-Output "Pages=$pageCount"
    Write-Output $pdfPath
}
finally {
    if ($null -ne $document) {
        $document.Close(0)
    }
    if ($null -ne $word) {
        $word.Quit()
    }
}
