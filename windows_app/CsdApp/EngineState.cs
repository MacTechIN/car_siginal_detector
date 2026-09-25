using System.Text.Json.Serialization;

namespace CsdApp;

// Mirrors csd/server.py build_state(). Korean display text comes from the engine.
public sealed class EngineState
{
    [JsonPropertyName("phase")] public string? Phase { get; set; }
    [JsonPropertyName("phase_text")] public string? PhaseText { get; set; }
    [JsonPropertyName("fps")] public double Fps { get; set; }
    [JsonPropertyName("camera")] public string? Camera { get; set; }
    [JsonPropertyName("frame_seq")] public long FrameSeq { get; set; }
    [JsonPropertyName("ego_text")] public string? EgoText { get; set; }
    [JsonPropertyName("lane")] public string? Lane { get; set; }
    [JsonPropertyName("lane_text")] public string? LaneText { get; set; }
    [JsonPropertyName("lane_detected")] public bool LaneDetected { get; set; }
    [JsonPropertyName("lane_pos")] public double? LanePos { get; set; }
    [JsonPropertyName("light")] public LightState? Light { get; set; }
    [JsonPropertyName("lead")] public LeadState? Lead { get; set; }
    [JsonPropertyName("risk")] public RiskState? Risk { get; set; }
    [JsonPropertyName("scene")] public Dictionary<string, int>? Scene { get; set; }
    [JsonPropertyName("caption")] public Caption? Caption { get; set; }
    [JsonPropertyName("objects")] public List<TrackedObject>? Objects { get; set; }
    [JsonPropertyName("events")] public List<EventLine>? Events { get; set; }
    [JsonPropertyName("labeling")] public LabelingState? Labeling { get; set; }
}

public sealed class LightState
{
    [JsonPropertyName("id")] public int Id { get; set; }
    [JsonPropertyName("state")] public string? State { get; set; }
    [JsonPropertyName("text")] public string? Text { get; set; }
}

public sealed class LeadState
{
    [JsonPropertyName("id")] public int Id { get; set; }
    [JsonPropertyName("motion_text")] public string? MotionText { get; set; }
    [JsonPropertyName("plate")] public string? Plate { get; set; }
    [JsonPropertyName("brake")] public bool Brake { get; set; }
    [JsonPropertyName("left")] public bool Left { get; set; }
    [JsonPropertyName("right")] public bool Right { get; set; }
    [JsonPropertyName("hazard")] public bool Hazard { get; set; }
    [JsonPropertyName("collision")] public string? Collision { get; set; }
    [JsonPropertyName("ttc")] public double? Ttc { get; set; }
}

public sealed class RiskState
{
    [JsonPropertyName("level")] public string? Level { get; set; }
    [JsonPropertyName("text")] public string? Text { get; set; }
    [JsonPropertyName("age_s")] public double? AgeS { get; set; }
}

public sealed class Caption
{
    [JsonPropertyName("text")] public string? Text { get; set; }
    [JsonPropertyName("priority")] public string? Priority { get; set; }
    [JsonPropertyName("age_s")] public double AgeS { get; set; }
}

public sealed class TrackedObject
{
    [JsonPropertyName("id")] public int Id { get; set; }
    [JsonPropertyName("name_text")] public string? NameText { get; set; }
    [JsonPropertyName("state_text")] public string? StateText { get; set; }
    [JsonPropertyName("plate")] public string? Plate { get; set; }
    [JsonPropertyName("lead")] public bool Lead { get; set; }
}

public sealed class EventLine
{
    [JsonPropertyName("t")] public double T { get; set; }
    [JsonPropertyName("line")] public string? Line { get; set; }

    public string Display =>
        $"{DateTimeOffset.FromUnixTimeMilliseconds((long)(T * 1000)).ToLocalTime():HH:mm:ss}   {Line}";
}

public sealed class LabelingState
{
    [JsonPropertyName("listening")] public bool Listening { get; set; }
    [JsonPropertyName("error")] public string? Error { get; set; }
    [JsonPropertyName("heard")] public string? Heard { get; set; }
    [JsonPropertyName("counts")] public Dictionary<string, int>? Counts { get; set; }
}
