using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;
using System.Windows.Threading;

namespace CsdApp;

public partial class MainWindow : Window
{
    private static readonly Brush Red = Hex("#EB4137"), Yellow = Hex("#F5BE28"), Green = Hex("#37C869"),
        Orange = Hex("#F58723"), Blue = Hex("#4696F5"), Off = Hex("#3E4249"), Dim = Hex("#878E98"),
        Text = Hex("#EBEDF0"), PlateBg = Hex("#F5F5F0");

    // Lamps lit per state: red, yellow, left arrow, green (Korean 4-lamp head)
    private static readonly Dictionary<string, bool[]> Lamps = new()
    {
        ["red"] = [true, false, false, false], ["yellow"] = [false, true, false, false],
        ["green"] = [false, false, false, true], ["left"] = [false, false, true, false],
        ["green_left"] = [false, false, true, true], ["red_left"] = [true, false, true, false],
        ["red_yellow"] = [true, true, false, false], ["flashing_yellow"] = [false, true, false, false],
        ["flashing_red"] = [true, false, false, false],
    };

    private readonly Engine? _engine;
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromMilliseconds(200) };
    private long _lastFrameSeq = -1;
    private bool _polling, _closing;
    private int _tick;

    public MainWindow()
    {
        InitializeComponent();
        var root = Engine.FindProjectRoot();
        if (root == null)
        {
            StatusText.Text = "프로젝트 폴더를 찾지 못했습니다 (csd 폴더가 있는 상위 폴더에서 실행하세요)";
            StartButton.IsEnabled = false;
            return;
        }
        _engine = new Engine(root);
        if (!File.Exists(_engine.PythonExe))
        {
            StatusText.Text = $"Python 가상환경이 없습니다: {_engine.PythonExe}";
            StartButton.IsEnabled = false;
        }
        _engine.LogLine += line => Dispatcher.BeginInvoke(() => AppendLog(line));
        _engine.Exited += code => Dispatcher.BeginInvoke(() => OnEngineExited(code));
        _timer.Tick += async (_, _) => await PollAsync();
        _timer.Start();
        AppendLog($"[앱] 프로젝트: {root}");
        Loaded += (_, _) => ApplyCommandLine();
    }

    /// <summary>
    /// --autostart                     start the engine as soon as the window opens (desktop shortcut)
    /// --snapshot FILE [--snapshot-after SEC]   save a PNG of the window (works on a locked screen)
    /// --exit-after SEC                stop the engine cleanly and close
    /// </summary>
    private void ApplyCommandLine()
    {
        var args = Environment.GetCommandLineArgs();
        string? Arg(string name) { var i = Array.IndexOf(args, name); return i >= 0 && i + 1 < args.Length ? args[i + 1] : null; }
        if (args.Contains("--autostart")) StartEngine();
        if (Arg("--snapshot") is string file)
        {
            var after = double.TryParse(Arg("--snapshot-after"), out var s) ? s : 20;
            After(after, () => SaveSnapshot(file));
        }
        if (double.TryParse(Arg("--exit-after"), out var exitAfter)) After(exitAfter, Close);
    }

    private static void After(double seconds, Action action)
    {
        var t = new DispatcherTimer { Interval = TimeSpan.FromSeconds(seconds) };
        t.Tick += (_, _) => { t.Stop(); action(); };
        t.Start();
    }

    private void SaveSnapshot(string file)
    {
        var el = (FrameworkElement)Content;
        var rtb = new RenderTargetBitmap((int)el.ActualWidth, (int)el.ActualHeight, 96, 96, PixelFormats.Pbgra32);
        rtb.Render(el);
        var enc = new PngBitmapEncoder();
        enc.Frames.Add(BitmapFrame.Create(rtb));
        using var fs = File.Create(file);
        enc.Save(fs);
        AppendLog($"[앱] 화면 저장: {file}");
    }

    private static SolidColorBrush Hex(string hex)
    {
        var b = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));
        b.Freeze();
        return b;
    }

    // ------------------------------------------------------------------ engine control
    private void Start_Click(object sender, RoutedEventArgs e) => StartEngine();

    private void StartEngine()
    {
        if (_engine == null || _engine.Running) return;
        _lastFrameSeq = -1;
        _engine.Start(VoiceCheck.IsChecked == true, RecordCheck.IsChecked == true, LabelCheck.IsChecked == true);
        StartButton.IsEnabled = false;
        StopButton.IsEnabled = true;
        SetOptionsEnabled(false);
        StatusText.Text = "엔진 시작 중…";
        VideoOverlay.Text = "엔진 시작 중…";
        VideoOverlay.Visibility = Visibility.Visible;
    }

    private async void Stop_Click(object sender, RoutedEventArgs e) => await StopEngineAsync();

    private async Task StopEngineAsync()
    {
        if (_engine == null || !_engine.Running) return;
        StopButton.IsEnabled = false;
        StatusText.Text = "정지 중… (녹화 저장)";
        await _engine.StopAsync();
    }

    private void OnEngineExited(int code)
    {
        StartButton.IsEnabled = !_closing;
        StopButton.IsEnabled = false;
        SetOptionsEnabled(true);
        StatusText.Text = code == 0 ? "정지됨" : $"엔진 종료 (코드 {code}) — 엔진 로그 확인";
        VideoOverlay.Text = "정지됨";
        VideoOverlay.Visibility = Visibility.Visible;
        DangerBorder.BorderThickness = new Thickness(0);
        AppendLog($"[앱] 엔진 종료 (코드 {code})");
        if (_closing) Close();
    }

    private void SetOptionsEnabled(bool on)
    {
        VoiceCheck.IsEnabled = RecordCheck.IsEnabled = LabelCheck.IsEnabled = on;
    }

    private async void Window_Closing(object? sender, CancelEventArgs e)
    {
        if (_engine is { Running: true })
        {
            // Stop the engine first so the recording and labels are saved, then close.
            e.Cancel = true;
            if (_closing) return;
            _closing = true;
            await StopEngineAsync();
        }
        else
        {
            _timer.Stop();
            _engine?.Dispose();
        }
    }

    private async void Window_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.F5) StartEngine();
        else if (e.Key == Key.Escape) await StopEngineAsync();
    }

    private void Preflight_Click(object sender, RoutedEventArgs e)
    {
        if (_engine == null) return;
        if (_engine.Running)
        {
            MessageBox.Show(this, "엔진이 카메라를 사용 중입니다. 정지한 뒤 점검하세요.", "출발 전 점검",
                MessageBoxButton.OK, MessageBoxImage.Information);
            return;
        }
        _engine.RunInConsole("출발 전 점검", @".venv\Scripts\python.exe tools\preflight.py");
    }

    private void Train_Click(object sender, RoutedEventArgs e)
    {
        if (_engine == null) return;
        var ok = MessageBox.Show(this,
            "음성 라벨로 모은 신호등 크롭으로 분류기를 학습합니다 (CPU, 몇 분).\n학습된 모델은 다음 시작부터 자동으로 사용됩니다.",
            "학습", MessageBoxButton.OKCancel, MessageBoxImage.Question);
        if (ok == MessageBoxResult.OK) _engine.RunInConsole("신호등 분류기 학습", "call train.bat");
    }

    private void Recordings_Click(object sender, RoutedEventArgs e)
    {
        if (_engine == null) return;
        var dir = System.IO.Path.Combine(_engine.ProjectRoot, "recordings");
        Directory.CreateDirectory(dir);
        Process.Start(new ProcessStartInfo("explorer.exe", $"\"{dir}\"") { UseShellExecute = true });
    }

    // ------------------------------------------------------------------ polling
    private async Task PollAsync()
    {
        if (_polling || _engine == null) return;
        _polling = true;
        _tick++;
        try
        {
            if (!_engine.Running) return;
            var s = await _engine.GetStateAsync();
            if (s == null) return;
            UpdateState(s);
            if (s.FrameSeq != _lastFrameSeq && s.FrameSeq > 0)
            {
                var jpg = await _engine.GetFrameAsync();
                if (jpg != null)
                {
                    _lastFrameSeq = s.FrameSeq;
                    VideoImage.Source = Decode(jpg);
                    VideoOverlay.Visibility = Visibility.Collapsed;
                }
            }
        }
        finally
        {
            _polling = false;
        }
    }

    private static BitmapImage Decode(byte[] jpg)
    {
        var bmp = new BitmapImage();
        using var ms = new MemoryStream(jpg);
        bmp.BeginInit();
        bmp.CacheOption = BitmapCacheOption.OnLoad;
        bmp.StreamSource = ms;
        bmp.EndInit();
        bmp.Freeze();
        return bmp;
    }

    private void UpdateState(EngineState s)
    {
        bool blink = _tick % 4 < 2;  // ~2.5 Hz with the 200 ms timer

        if (s.Phase != "running")
        {
            StatusText.Text = s.PhaseText ?? "엔진 준비 중";
            VideoOverlay.Text = s.PhaseText ?? "엔진 준비 중";
            return;
        }
        StatusText.Text = $"카메라 {s.Camera}   {s.Fps:0.0} fps   {DateTime.Now:HH:mm:ss}";

        // Caption (last spoken alert, large for 5 s)
        if (s.Caption is { } c && c.AgeS <= 5)
        {
            CaptionBar.Background = c.Priority switch
            {
                "danger" => Hex("#C81E1E"), "warning" => Hex("#D76E14"), "signal" => Hex("#235FBE"), _ => Hex("#373C46"),
            };
            CaptionLabel.Text = "음성 안내";
            CaptionText.Text = c.Text;
            CaptionText.Foreground = Brushes.White;
        }
        else
        {
            CaptionBar.Background = Hex("#1C1F24");
            CaptionLabel.Text = "음성 안내";
            CaptionText.Text = s.Caption != null ? $"마지막 안내: {s.Caption.Text}" : "안내 대기 중";
            CaptionText.Foreground = Dim;
        }

        // Traffic light
        var state = s.Light?.State ?? "";
        bool[] lit = Lamps.TryGetValue(state, out var l) ? l : new bool[4];
        bool flashOff = state.StartsWith("flashing") && !blink;
        Ellipse[] lamps = [LampRed, LampYellow, LampLeft, LampGreen];
        Brush[] colors = [Red, Yellow, Green, Green];
        for (int i = 0; i < 4; i++) lamps[i].Fill = lit[i] && !flashOff ? colors[i] : Off;
        LightText.Text = s.Light?.Text ?? "감지 없음";
        LightText.Foreground = s.Light == null ? Dim
            : state.Contains("red") ? Red : state.Contains("yellow") ? Yellow : state.Length > 0 && state != "off" ? Green : Text;

        // Risk
        var level = s.Risk?.Level ?? "none";
        var riskBrush = level == "danger" ? Red : level == "warning" ? Orange : Green;
        RiskCard.BorderBrush = riskBrush;
        RiskLevelText.Text = level == "danger" ? "위험" : level == "warning" ? "주의" : "안전";
        RiskLevelText.Foreground = riskBrush;
        var ttc = s.Lead?.Ttc;
        TtcText.Text = ttc is double t ? $"충돌까지 {t:0.0}초" : "충돌 위험 없음";
        TtcBar.Value = ttc is double t2 ? 1.0 - Math.Min(t2, 5.0) / 5.0 : 0;
        TtcBar.Foreground = riskBrush;
        RiskEventText.Text = s.Risk?.Text is string rt ? $"{rt}  ({s.Risk.AgeS:0}초 전)" : "최근 위험 이벤트 없음";
        RiskEventText.Foreground = s.Risk?.Text != null ? Orange : Dim;
        DangerBorder.BorderThickness = new Thickness(s.Lead?.Collision == "danger" && blink ? 12 : 0);

        // Lead vehicle
        if (s.Lead is { } lead)
        {
            LeadIdText.Text = $"ID {lead.Id}";
            LeadIdText.Foreground = Text;
            LeadMotionText.Text = lead.MotionText;
            PlateBox.Background = lead.Plate != null ? PlateBg : Off;
            PlateText.Text = lead.Plate ?? "번호 인식 중";
            PlateText.Foreground = lead.Plate != null ? Brushes.Black : Dim;
            ChipBrake.Background = lead.Brake ? Red : Off;
            ChipLeft.Background = lead.Left && blink ? Orange : Off;
            ChipHazard.Background = lead.Hazard && blink ? Orange : Off;
            ChipRight.Background = lead.Right && blink ? Orange : Off;
        }
        else
        {
            LeadIdText.Text = "앞차 없음";
            LeadIdText.Foreground = Dim;
            LeadMotionText.Text = "";
            PlateBox.Background = Off;
            PlateText.Text = "-";
            PlateText.Foreground = Dim;
            ChipBrake.Background = ChipLeft.Background = ChipHazard.Background = ChipRight.Background = Off;
        }

        // Lane + ego
        bool departure = s.Lane?.StartsWith("departure") == true;
        LaneText.Text = s.LaneText ?? "차선 미검출";
        LaneText.Foreground = departure ? Orange : s.Lane != null ? Green : Dim;
        EgoText.Text = $"자차 {s.EgoText}";
        var lineBrush = s.LaneDetected ? Brushes.WhiteSmoke : Off;
        LaneLeftLine.Stroke = LaneRightLine.Stroke = lineBrush;
        Canvas.SetLeft(EgoCar, 20 + 80 * (s.LanePos ?? 0.5) - 9);
        EgoCar.Fill = departure ? Orange : Blue;

        // Analysis table + scene
        var sc = s.Scene ?? new();
        SceneText.Text = $"분석 결과   차량 {sc.GetValueOrDefault("vehicles")} · 보행자 {sc.GetValueOrDefault("person")}" +
                         $" · 이륜차 {sc.GetValueOrDefault("two_wheeler")}";
        ObjectList.ItemsSource = s.Objects;

        // Events (newest first)
        EventList.ItemsSource = s.Events;

        // Voice labelling
        if (s.Labeling is { } lb)
        {
            LabelCard.Visibility = Visibility.Visible;
            LabelStatusText.Text = lb.Error != null ? $"오류: {lb.Error}" : lb.Listening ? "듣는 중 — 신호등 상태를 말하세요" : "준비 중";
            LabelStatusText.Foreground = lb.Error != null ? Red : lb.Listening ? Green : Dim;
            LabelHeardText.Text = $"들은 말: {lb.Heard ?? "-"}";
            LabelCountsText.Text = lb.Counts is { Count: > 0 }
                ? "저장된 크롭: " + string.Join("  ", lb.Counts.Select(kv => $"{kv.Key} {kv.Value}"))
                : "저장된 크롭: 없음";
        }
        else
        {
            LabelCard.Visibility = Visibility.Collapsed;
        }
    }

    private void AppendLog(string line)
    {
        EngineLog.AppendText(line + Environment.NewLine);
        if (EngineLog.LineCount > 400)
        {
            var text = EngineLog.Text;
            EngineLog.Text = text[(text.Length / 2)..];
        }
        EngineLog.ScrollToEnd();
    }
}
