using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace CsdApp;

/// <summary>
/// Runs the Python recognition engine (python -m csd --serve PORT) as a hidden child process
/// and talks to it over 127.0.0.1: GET /state, GET /frame.jpg, POST /shutdown.
/// </summary>
public sealed class Engine : IDisposable
{
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(2) };
    private Process? _proc;

    public string ProjectRoot { get; }
    public string PythonExe => Path.Combine(ProjectRoot, ".venv", "Scripts", "python.exe");
    public int Port { get; private set; }
    public bool Running => _proc is { HasExited: false };
    public event Action<string>? LogLine;
    public event Action<int>? Exited;

    public Engine(string projectRoot) => ProjectRoot = projectRoot;

    /// <summary>The repository root: the first parent folder of the exe that contains csd/__main__.py.</summary>
    public static string? FindProjectRoot()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
        {
            if (File.Exists(Path.Combine(dir.FullName, "csd", "__main__.py")))
                return dir.FullName;
        }
        return null;
    }

    private static int FreePort()
    {
        var l = new TcpListener(IPAddress.Loopback, 0);
        l.Start();
        var port = ((IPEndPoint)l.LocalEndpoint).Port;
        l.Stop();
        return port;
    }

    public void Start(bool voice, bool record, bool labelVoice)
    {
        if (Running) return;
        Port = FreePort();
        var args = new StringBuilder($"-m csd --serve {Port} --name app");
        if (!voice) args.Append(" --no-voice");
        if (record) args.Append(" --record");
        if (labelVoice) args.Append(" --label-voice");

        var psi = new ProcessStartInfo(PythonExe, args.ToString())
        {
            WorkingDirectory = ProjectRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.Environment["PYTHONUNBUFFERED"] = "1";

        _proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
        _proc.OutputDataReceived += (_, e) => { if (e.Data != null) LogLine?.Invoke(e.Data); };
        _proc.ErrorDataReceived += (_, e) => { if (e.Data != null) LogLine?.Invoke(e.Data); };
        _proc.Exited += (_, _) => Exited?.Invoke(SafeExitCode(_proc));
        _proc.Start();
        _proc.BeginOutputReadLine();
        _proc.BeginErrorReadLine();
        LogLine?.Invoke($"[앱] 엔진 시작: python {args}");
    }

    private static int SafeExitCode(Process? p)
    {
        try { return p?.ExitCode ?? -1; } catch { return -1; }
    }

    private string Url(string path) => $"http://127.0.0.1:{Port}{path}";

    public async Task<EngineState?> GetStateAsync()
    {
        if (!Running) return null;
        try
        {
            var json = await _http.GetStringAsync(Url("/state"));
            return JsonSerializer.Deserialize<EngineState>(json);
        }
        catch
        {
            return null;  // engine still starting or busy
        }
    }

    public async Task<byte[]?> GetFrameAsync()
    {
        if (!Running) return null;
        try
        {
            using var r = await _http.GetAsync(Url("/frame.jpg"));
            return r.StatusCode == HttpStatusCode.OK ? await r.Content.ReadAsByteArrayAsync() : null;
        }
        catch
        {
            return null;
        }
    }

    /// <summary>Ask the engine to stop (so recordings and labels are closed properly); kill after a timeout.</summary>
    public async Task StopAsync(int timeoutMs = 10000)
    {
        if (!Running) return;
        LogLine?.Invoke("[앱] 엔진 정지 요청");
        try { await _http.PostAsync(Url("/shutdown"), null); } catch { /* not listening yet */ }
        var p = _proc!;
        if (!await Task.Run(() => p.WaitForExit(timeoutMs)))
        {
            LogLine?.Invoke("[앱] 응답이 없어 엔진을 강제 종료합니다");
            try { p.Kill(entireProcessTree: true); } catch { }
        }
    }

    /// <summary>Run a helper in its own console window (preflight, training).</summary>
    public void RunInConsole(string title, string commandLine)
    {
        var psi = new ProcessStartInfo("cmd.exe",
            $"/k chcp 65001 >nul & title {title} & set PYTHONIOENCODING=utf-8 & {commandLine}")
        {
            WorkingDirectory = ProjectRoot,
            UseShellExecute = true,
        };
        Process.Start(psi);
    }

    public void Dispose()
    {
        if (Running)
        {
            try { _http.PostAsync(Url("/shutdown"), null).Wait(1500); } catch { }
            try { if (!_proc!.WaitForExit(8000)) _proc.Kill(entireProcessTree: true); } catch { }
        }
        _http.Dispose();
    }
}
