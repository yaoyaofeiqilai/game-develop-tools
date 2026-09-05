param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [string]$OutputPath = ".\output",
    [ValidateSet("smart", "grid")]
    [string]$Segmentation = "smart",
    [int]$Rows = 0,
    [int]$Columns = 0,
    [int]$ExpectedFrames = 0,
    [ValidateSet("rembg", "chroma", "auto")]
    [string]$Mode = "auto",
    [string]$Model = "isnet-anime",
    [ValidateSet("auto", "cuda", "cpu")]
    [string]$Provider = "auto"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$workflowPath = Join-Path $projectRoot "sprite_workflow.py"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "未找到项目虚拟环境：$pythonPath"
}

& $pythonPath $workflowPath $InputPath `
    --output $OutputPath `
    --segmentation $Segmentation `
    --rows $Rows `
    --columns $Columns `
    --expected-frames $ExpectedFrames `
    --mode $Mode `
    --model $Model `
    --provider $Provider

exit $LASTEXITCODE
