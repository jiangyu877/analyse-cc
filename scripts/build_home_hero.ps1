param(
    [string]$InputPath = "C:\Users\jiang\Desktop\ScreenShot_2026-07-11_191023_048.png",
    [string]$OutputPath = "D:\project\consumer_analysis_web\app\static\images\retail-hero-processed.jpg"
)

Add-Type -AssemblyName System.Drawing

$source = [System.Drawing.Bitmap]::new($InputPath)
$target = [System.Drawing.Bitmap]::new(2000, 1125, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
$graphics = [System.Drawing.Graphics]::FromImage($target)
$graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
$graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$graphics.DrawImage(
    $source,
    [System.Drawing.Rectangle]::new(0, 0, 2000, 1125),
    [System.Drawing.Rectangle]::new(38, 90, 1490, 838),
    [System.Drawing.GraphicsUnit]::Pixel
)
$graphics.Dispose()
$source.Dispose()

$rect = [System.Drawing.Rectangle]::new(0, 0, $target.Width, $target.Height)
$data = $target.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::ReadWrite, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
$bytes = [Math]::Abs($data.Stride) * $target.Height
$buffer = [byte[]]::new($bytes)
[System.Runtime.InteropServices.Marshal]::Copy($data.Scan0, $buffer, 0, $bytes)

for ($y = 0; $y -lt $target.Height; $y++) {
    $row = $y * $data.Stride
    for ($x = 0; $x -lt $target.Width; $x++) {
        $i = $row + ($x * 3)
        $b = [int]$buffer[$i]
        $g = [int]$buffer[$i + 1]
        $r = [int]$buffer[$i + 2]
        $max = [Math]::Max($r, [Math]::Max($g, $b))
        $min = [Math]::Min($r, [Math]::Min($g, $b))

        # Neutral bright pixels belong primarily to the source page typography.
        $scale = if ($min -gt 105 -and ($max - $min) -lt 30) { 0.16 } else { 0.76 }
        $buffer[$i] = [byte][Math]::Min(255, [Math]::Round($b * $scale))
        $buffer[$i + 1] = [byte][Math]::Min(255, [Math]::Round($g * $scale))
        $buffer[$i + 2] = [byte][Math]::Min(255, [Math]::Round($r * $scale))
    }
}

[System.Runtime.InteropServices.Marshal]::Copy($buffer, 0, $data.Scan0, $bytes)
$target.UnlockBits($data)

$jpegCodec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object MimeType -eq "image/jpeg"
$encoder = [System.Drawing.Imaging.Encoder]::Quality
$parameters = [System.Drawing.Imaging.EncoderParameters]::new(1)
$parameters.Param[0] = [System.Drawing.Imaging.EncoderParameter]::new($encoder, [long]90)
$target.Save($OutputPath, $jpegCodec, $parameters)
$parameters.Dispose()
$target.Dispose()

Write-Output $OutputPath
