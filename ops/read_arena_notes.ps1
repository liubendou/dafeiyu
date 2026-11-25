Add-Type -AssemblyName UIAutomationClient
$root = [System.Windows.Automation.AutomationElement]::RootElement
$trueCondition = [System.Windows.Automation.Condition]::TrueCondition
$windows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $trueCondition)
$pattern = '(?i)(deepseek|claude|gpt|gemini|qwen|kimi|grok|glm|llama|mistral|command-r|sonnet|opus|haiku)[A-Za-z0-9._-]*'
$notes = @()
$windowCount = 0
for ($wIndex = 0; $wIndex -lt $windows.Count; $wIndex++) {
  try {
    $win = $windows.Item($wIndex)
    $windowName = [string]$win.Current.Name
    if ($windowName -notmatch 'Arena') { continue }
    $windowCount++
    $items = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $trueCondition)
    for ($i = 0; $i -lt $items.Count; $i++) {
      try {
        $el = $items.Item($i)
        if ($el.Current.ControlType -ne [System.Windows.Automation.ControlType]::ListItem) { continue }
        $class = [string]$el.Current.ClassName
        if ($class -notmatch 'group/menu-item') { continue }
        $name = [string]$el.Current.Name
        $match = [regex]::Match($name, $pattern)
        if (-not $match.Success) { continue }
        $notes += [pscustomobject]@{
          window_process_id = $win.Current.ProcessId
          row_index = $i
          model_note = $match.Value
          row_name = $name
        }
      } catch {}
    }
  } catch {}
}
$result = @{ ok = [bool]($notes.Count -gt 0); window_count = [int]$windowCount; notes = @($notes) }
ConvertTo-Json -InputObject $result -Depth 5 -Compress
