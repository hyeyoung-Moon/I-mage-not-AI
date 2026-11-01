#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.IO;
using UnityEditor;
using UnityEngine;

/// <summary>Exports keyframed Transform curves without instantiating a scene or invoking callbacks.</summary>
public static class i_mage_not_aiTraceExporter
{
    private sealed class Curves
    {
        public readonly Dictionary<string, AnimationCurve> Position = new Dictionary<string, AnimationCurve>();
        public readonly Dictionary<string, AnimationCurve> Rotation = new Dictionary<string, AnimationCurve>();
        public readonly Dictionary<string, AnimationCurve> Scale = new Dictionary<string, AnimationCurve>();
    }

    [MenuItem("Assets/Export AnimationClip as i_mage_not_ai Trace")]
    private static void ExportSelectedClip()
    {
        var clip = Selection.activeObject as AnimationClip;
        if (clip == null) { Debug.LogError("Select an AnimationClip first."); return; }
        try
        {
            var json = BuildExport(clip);
            var path = EditorUtility.SaveFilePanel("Export i_mage_not_ai trace", "", clip.name + ".i_mage_not_ai.json", "json");
            if (String.IsNullOrEmpty(path)) return;
            System.IO.File.WriteAllText(path, json, new UTF8Encoding(false));
            Debug.Log("Exported i_mage_not_ai trace: " + path);
        }
        catch (Exception exception) { Debug.LogError("i_mage_not_ai export failed: " + exception.Message); }
    }

    /// <summary>Batch-mode entry point for an explicitly selected project AnimationClip.</summary>
    public static void ExportFromCommandLine()
    {
        try
        {
            var args = Environment.GetCommandLineArgs();
            var clipPath = ReadArgument(args, "-i_mage_not_aiClip");
            var outputPath = ReadArgument(args, "-i_mage_not_aiOutput");
            if (String.IsNullOrWhiteSpace(clipPath) || String.IsNullOrWhiteSpace(outputPath))
                throw new ArgumentException("Pass -i_mage_not_aiClip Assets/path/clip.anim and -i_mage_not_aiOutput <absolute-json-path>.");
            if (!clipPath.StartsWith("Assets/", StringComparison.Ordinal) || clipPath.Contains(".."))
                throw new ArgumentException("AnimationClip path must stay under Assets/.");
            var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath);
            if (clip == null) throw new FileNotFoundException("AnimationClip was not found or could not be imported: " + clipPath);
            var fullOutputPath = Path.GetFullPath(outputPath);
            Directory.CreateDirectory(Path.GetDirectoryName(fullOutputPath));
            File.WriteAllText(fullOutputPath, BuildExport(clip), new UTF8Encoding(false));
            Debug.Log("I_MAGE_NOT_AI_EXPORT_PASS Unity " + Application.unityVersion + " output=" + fullOutputPath);
        }
        catch (Exception exception)
        {
            Debug.LogError("i_mage_not_ai command-line export failed: " + exception);
            throw;
        }
    }

    private static string ReadArgument(string[] args, string name)
    {
        var index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }

    public static string BuildExport(AnimationClip clip)
    {
        if (clip == null) throw new ArgumentNullException(nameof(clip));
        var nodes = new SortedDictionary<string, Curves>(StringComparer.Ordinal);
        foreach (var binding in AnimationUtility.GetCurveBindings(clip))
        {
            var curve = AnimationUtility.GetEditorCurve(clip, binding);
            if (curve == null) continue;
            var property = binding.propertyName;
            var target = property.StartsWith("m_LocalPosition.", StringComparison.Ordinal) ? "position" :
                        property.StartsWith("m_LocalRotation.", StringComparison.Ordinal) ? "rotation" :
                        property.StartsWith("m_LocalScale.", StringComparison.Ordinal) ? "scale" : null;
            if (target == null) continue;
            var axis = property.Substring(property.LastIndexOf('.') + 1);
            if (!new[] { "x", "y", "z", "w" }.Contains(axis)) continue;
            if (!nodes.ContainsKey(binding.path)) nodes[binding.path] = new Curves();
            var channels = target == "position" ? nodes[binding.path].Position : target == "rotation" ? nodes[binding.path].Rotation : nodes[binding.path].Scale;
            channels[axis] = curve;
        }

        var exportedPaths = new HashSet<string>(nodes.Where(pair => pair.Value.Position.Count > 0).Select(pair => pair.Key), StringComparer.Ordinal);
        var trackIds = exportedPaths.ToDictionary(path => path, path => clip.name + "::" + path, StringComparer.Ordinal);
        var tracks = new List<string>();
        foreach (var pair in nodes)
        {
            var path = pair.Key; var curves = pair.Value;
            if (curves.Rotation.Count > 0)
            {
                var rotationTimes = curves.Rotation.Values.SelectMany(curve => curve.keys.Select(key => key.time)).Distinct().OrderBy(time => time);
                var rotationSamples = rotationTimes.Select(time =>
                {
                    var q = new Quaternion(Evaluate(curves.Rotation, "x", time, 0), Evaluate(curves.Rotation, "y", time, 0),
                        Evaluate(curves.Rotation, "z", time, 0), Evaluate(curves.Rotation, "w", time, 1));
                    q = LengthSquared(q) < 1e-12f ? Quaternion.identity : Normalize(q);
                    return "{\"time\":" + N(time) + ",\"frame_index\":" + Mathf.RoundToInt(time * clip.frameRate) + ",\"value\":[" + N(q.x) + "," + N(q.y) + "," + N(q.z) + "," + N(q.w) + "]}";
                }).ToArray();
                tracks.Add("{\"id\":" + Q(clip.name + "::" + path + "::rotation") + ",\"kind\":\"rotation\",\"units\":\"quaternion\",\"coordinate_system\":\"unity_local_3d\",\"samples\":[" + String.Join(",", rotationSamples) + "]}");
            }
            if (curves.Position.Count == 0) continue;
            var times = curves.Position.Values.Concat(curves.Rotation.Values).Concat(curves.Scale.Values)
                .SelectMany(curve => curve.keys.Select(key => key.time)).Distinct().OrderBy(time => time).ToArray();
            var samples = new List<string>();
            foreach (var time in times)
            {
                var position = new Vector3(Evaluate(curves.Position, "x", time, 0), Evaluate(curves.Position, "y", time, 0), Evaluate(curves.Position, "z", time, 0));
                var quaternion = new Quaternion(Evaluate(curves.Rotation, "x", time, 0), Evaluate(curves.Rotation, "y", time, 0), Evaluate(curves.Rotation, "z", time, 0), Evaluate(curves.Rotation, "w", time, 1));
                if (LengthSquared(quaternion) < 1e-12f) quaternion = Quaternion.identity; else quaternion = Normalize(quaternion);
                var scale = new Vector3(Evaluate(curves.Scale, "x", time, 1), Evaluate(curves.Scale, "y", time, 1), Evaluate(curves.Scale, "z", time, 1));
                var x = quaternion * Vector3.right * scale.x; var y = quaternion * Vector3.up * scale.y; var z = quaternion * Vector3.forward * scale.z;
                var matrix = "[[" + N(x.x) + "," + N(y.x) + "," + N(z.x) + "],[" + N(x.y) + "," + N(y.y) + "," + N(z.y) + "],[" + N(x.z) + "," + N(y.z) + "," + N(z.z) + "]]";
                samples.Add("{\"time\":" + N(time) + ",\"frame_index\":" + Mathf.RoundToInt(time * clip.frameRate) + ",\"value\":[" + N(position.x) + "," + N(position.y) + "," + N(position.z) + "],\"basis_matrix\":" + matrix + "}");
            }
            var track = "{\"id\":" + Q(trackIds[path]) + ",\"kind\":\"position\",\"units\":\"engine_unit\",\"coordinate_system\":\"unity_local_3d\",\"coordinate_space\":\"local\"";
            var parent = ParentPath(path);
            if (parent != path && trackIds.ContainsKey(parent)) track += ",\"parent_track_id\":" + Q(trackIds[parent]);
            track += ",\"samples\":[" + String.Join(",", samples) + "]}";
            tracks.Add(track);
        }

        var events = AnimationUtility.GetAnimationEvents(clip).Select((item, index) =>
            "{\"id\":" + Q(clip.name + "::event::" + index) + ",\"event_type\":" + Q(item.functionName ?? "") + ",\"time\":" + N(item.time) + "}").ToArray();
        var trace = "{\"schema_version\":\"1\",\"timebase\":\"seconds\",\"tracks\":[" + String.Join(",", tracks) + "],\"events\":[" + String.Join(",", events) + "]}";
        return "{\"format\":\"i_mage_not_ai.unity-export/1\",\"engine_version\":" + Q(Application.unityVersion) + ",\"trace\":" + trace + "}";
    }

    private static float Evaluate(Dictionary<string, AnimationCurve> curves, string axis, float time, float fallback)
    {
        if (!curves.TryGetValue(axis, out var curve)) return fallback;
        var value = curve.Evaluate(time);
        if (Single.IsNaN(value) || Single.IsInfinity(value)) throw new InvalidOperationException("animation curve contains a non-finite value");
        return value;
    }

    private static Quaternion Normalize(Quaternion q)
    {
        var length = Mathf.Sqrt(q.x*q.x + q.y*q.y + q.z*q.z + q.w*q.w);
        return new Quaternion(q.x/length, q.y/length, q.z/length, q.w/length);
    }

    private static float LengthSquared(Quaternion q)
    {
        return q.x*q.x + q.y*q.y + q.z*q.z + q.w*q.w;
    }

    private static string ParentPath(string path)
    {
        var index = path.LastIndexOf('/');
        return index < 0 ? String.Empty : path.Substring(0, index);
    }

    private static string N(float value)
    {
        if (Single.IsNaN(value) || Single.IsInfinity(value)) throw new InvalidOperationException("export value is non-finite");
        return value.ToString("R", CultureInfo.InvariantCulture);
    }

    private static string Q(string value)
    {
        var result = new StringBuilder("\"");
        foreach (var character in value ?? String.Empty)
        {
            switch (character)
            {
                case '"': result.Append("\\\""); break;
                case '\\': result.Append("\\\\"); break;
                case '\b': result.Append("\\b"); break;
                case '\f': result.Append("\\f"); break;
                case '\n': result.Append("\\n"); break;
                case '\r': result.Append("\\r"); break;
                case '\t': result.Append("\\t"); break;
                default:
                    if (character < 0x20) result.Append("\\u" + ((int)character).ToString("x4", CultureInfo.InvariantCulture));
                    else result.Append(character);
                    break;
            }
        }
        return result.Append('"').ToString();
    }
}
#endif
