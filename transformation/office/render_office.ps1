param([string]$Source,[string]$Destination,[string]$Report)
$ErrorActionPreference = 'Stop'
$officeApp = $null
$officeDocument = $null
$ownedApp = $false
function Measure-TextShape($shape, $slideNumber, $path) {
    if ($shape.Type -eq 6) {
        foreach ($child in $shape.GroupItems) { Measure-TextShape $child $slideNumber ($path + '/' + $child.Id) }
    }
    if ($shape.HasTable) {
        for ($row=1; $row -le $shape.Table.Rows.Count; $row++) {
            for ($column=1; $column -le $shape.Table.Columns.Count; $column++) {
                Measure-TextShape $shape.Table.Cell($row,$column).Shape $slideNumber ($path + '/cell-' + $row + '-' + $column)
            }
        }
    }
    if ($shape.HasTextFrame -and $shape.TextFrame2.HasText) {
        $frame = $shape.TextFrame2
        if ($frame.TextRange.BoundHeight -gt ($shape.Height - $frame.MarginTop - $frame.MarginBottom + 1)) {
            $script:overflow += @{slide=$slideNumber;shape=$shape.Id;path=$path;reason='text exceeds shape height';ratio=($shape.Height - $frame.MarginTop - $frame.MarginBottom)/$frame.TextRange.BoundHeight}
        }
        if ($frame.TextRange.BoundWidth -gt ($shape.Width - $frame.MarginLeft - $frame.MarginRight + 1)) {
            $script:overflow += @{slide=$slideNumber;shape=$shape.Id;path=$path;reason='text exceeds shape width';ratio=($shape.Width - $frame.MarginLeft - $frame.MarginRight)/$frame.TextRange.BoundWidth}
        }
    }
}
try {
    $overflow = @()
    if ([IO.Path]::GetExtension($Source) -eq '.docx') {
        $officeApp = New-Object -ComObject Word.Application
        $ownedApp = ($officeApp.Documents.Count -eq 0)
        if ($ownedApp) { $officeApp.Visible = $false }
        $officeApp.DisplayAlerts = 0
        $officeApp.AutomationSecurity = 3
        $officeApp.Options.UpdateLinksAtOpen = $false
        $officeDocument = $officeApp.Documents.Open($Source, $false, $true, $false)
        $officeDocument.Repaginate()
        $officeDocument.ExportAsFixedFormat($Destination, 17)
    } else {
        $officeApp = New-Object -ComObject PowerPoint.Application
        $ownedApp = ($officeApp.Presentations.Count -eq 0)
        $officeApp.AutomationSecurity = 3
        $officeDocument = $officeApp.Presentations.Open($Source, $true, $false, $false)
        foreach ($slide in $officeDocument.Slides) {
            foreach ($shape in $slide.Shapes) {
                Measure-TextShape $shape $slide.SlideIndex ([string]$shape.Id)
            }
        }
        $officeDocument.SaveAs($Destination, 32)
    }
    @{renderer='Microsoft Office';overflow=$overflow} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Report -Encoding UTF8
} finally {
    if ($null -ne $officeDocument) { $officeDocument.Close(); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($officeDocument) }
    if ($null -ne $officeApp) {
        if ($ownedApp) {
            if ([IO.Path]::GetExtension($Source) -eq '.docx') { if ($officeApp.Documents.Count -eq 0) { $officeApp.Quit() } }
            else { if ($officeApp.Presentations.Count -eq 0) { $officeApp.Quit() } }
        }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($officeApp)
    }
}
