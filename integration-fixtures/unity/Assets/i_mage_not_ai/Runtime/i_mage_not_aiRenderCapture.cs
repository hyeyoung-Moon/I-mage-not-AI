using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.Rendering;

public sealed class i_mage_not_aiRenderCapture : MonoBehaviour
{
    [Serializable] private sealed class CaptureFrame
    {
        public int frame_index;
        public float timestamp;
        public string image;
        public string[] event_ids;
    }

    [Serializable] private sealed class ImportSettings
    {
        public string render_pipeline;
        public string color_space;
        public int anti_aliasing;
        public int v_sync_count;
        public string capture_mode;
        public string camera_node_path;
        public float camera_distance_offset;
        public float camera_zoom_scale;
    }

    [Serializable] private sealed class Evidence
    {
        public string format = "i_mage_not_ai.render-evidence/1";
        public string capture_source = "engine_runtime";
        public string engine = "unity";
        public string engine_version;
        public string platform;
        public string scenario_id;
        public string timebase = "seconds";
        public int[] resolution;
        public ImportSettings import_settings;
        public CaptureFrame[] captures;
    }

    private static readonly List<string> PendingEvents = new List<string>();
    private string outputPath;
    private string scenarioId;
    private string framesDirectory;
    private int frameCount;
    private int fps;
    private int width;
    private int height;
    private int settleFrames = 3;
    private Camera captureCamera;
    private RenderTexture renderTarget;
    private readonly List<CaptureFrame> captureRows = new List<CaptureFrame>();
    private bool captureFinished;

    public static void MarkEvent(string eventId)
    {
        if (!String.IsNullOrWhiteSpace(eventId)) PendingEvents.Add(eventId);
    }

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Bootstrap()
    {
        var arguments = Environment.GetCommandLineArgs();
        if (!Contains(arguments, "-i_mage_not_aiOutput")) return;
        var owner = new GameObject("i_mage_not_aiRenderCapture");
        DontDestroyOnLoad(owner);
        owner.AddComponent<i_mage_not_aiRenderCapture>().Configure(arguments);
    }

    private void Configure(string[] arguments)
    {
        outputPath = GetArgument(arguments, "-i_mage_not_aiOutput");
        scenarioId = GetArgument(arguments, "-i_mage_not_aiScenario");
        frameCount = Int32.Parse(GetArgument(arguments, "-i_mage_not_aiFrames"));
        fps = Int32.Parse(GetArgument(arguments, "-i_mage_not_aiFps"));
        width = Int32.Parse(GetArgument(arguments, "-i_mage_not_aiWidth"));
        height = Int32.Parse(GetArgument(arguments, "-i_mage_not_aiHeight"));
        var cameraNodePath = GetOptionalArgument(arguments, "-i_mage_not_aiCameraNodePath");
        var cameraDistanceOffset = Single.Parse(GetOptionalArgument(arguments, "-i_mage_not_aiCameraDistanceOffset") ?? "0", System.Globalization.CultureInfo.InvariantCulture);
        var cameraZoomScale = Single.Parse(GetOptionalArgument(arguments, "-i_mage_not_aiCameraZoomScale") ?? "1", System.Globalization.CultureInfo.InvariantCulture);
        if (Single.IsNaN(cameraDistanceOffset) || Single.IsInfinity(cameraDistanceOffset) ||
            Single.IsNaN(cameraZoomScale) || Single.IsInfinity(cameraZoomScale) || cameraZoomScale <= 0)
        {
            Fail("Camera adjustments must be finite and camera zoom scale must be positive.");
            return;
        }
        framesDirectory = Path.Combine(Path.GetDirectoryName(outputPath), Path.GetFileNameWithoutExtension(outputPath) + "_frames");
        Application.targetFrameRate = fps;
        Time.captureFramerate = fps;
        var selectedCamera = String.IsNullOrEmpty(cameraNodePath) ? null : GameObject.Find(cameraNodePath);
        captureCamera = String.IsNullOrEmpty(cameraNodePath) ? Camera.main : (selectedCamera == null ? null : selectedCamera.GetComponent<Camera>());
        if (captureCamera == null)
        {
            Fail(String.IsNullOrEmpty(cameraNodePath) ? "No Camera.main was found in the selected scene." : "Camera node path did not resolve to a Camera: " + cameraNodePath);
            return;
        }
        if (cameraDistanceOffset != 0) captureCamera.transform.Translate(0, 0, -cameraDistanceOffset, Space.Self);
        if (captureCamera.orthographic)
            captureCamera.orthographicSize /= cameraZoomScale;
        else
            captureCamera.fieldOfView /= cameraZoomScale;
        if (captureCamera.fieldOfView <= 1 || captureCamera.fieldOfView >= 179)
        {
            Fail("Camera zoom scale produces a field of view outside (1, 179) degrees.");
            return;
        }
        captureCamera.aspect = (float)width / height;
        renderTarget = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32);
        renderTarget.Create();
        captureCamera.targetTexture = renderTarget;
    }

    private void Update()
    {
        if (captureFinished || captureCamera == null || renderTarget == null) return;
        Directory.CreateDirectory(framesDirectory);
        if (settleFrames > 0)
        {
            settleFrames--;
            return;
        }
        if (captureRows.Count >= frameCount)
        {
            FinishCapture();
            return;
        }
        captureCamera.Render();
        var previousTarget = RenderTexture.active;
        RenderTexture.active = renderTarget;
        var texture = new Texture2D(width, height, TextureFormat.RGB24, false);
        texture.ReadPixels(new Rect(0, 0, width, height), 0, 0);
        texture.Apply(false, false);
        RenderTexture.active = previousTarget;
        var frameIndex = captureRows.Count;
        var imageName = frameIndex.ToString("D6") + ".png";
        var imagePath = Path.Combine(framesDirectory, imageName);
        File.WriteAllBytes(imagePath, texture.EncodeToPNG());
        Destroy(texture);
        captureRows.Add(new CaptureFrame
        {
            frame_index = frameIndex,
            timestamp = (float)frameIndex / fps,
            image = Path.GetFileNameWithoutExtension(outputPath) + "_frames/" + imageName,
            event_ids = DrainEvents()
        });
        if (captureRows.Count == frameCount) FinishCapture();
    }

    private void FinishCapture()
    {
        if (captureFinished) return;
        captureFinished = true;
        var pipeline = GraphicsSettings.currentRenderPipeline;
        var evidence = new Evidence
        {
            engine_version = Application.unityVersion,
            platform = Application.platform.ToString(),
            scenario_id = scenarioId,
            resolution = new[] { width, height },
            import_settings = new ImportSettings
            {
                render_pipeline = pipeline == null ? "built-in" : pipeline.name,
                color_space = QualitySettings.activeColorSpace.ToString(),
                anti_aliasing = QualitySettings.antiAliasing,
                v_sync_count = QualitySettings.vSyncCount,
                capture_mode = "camera_render_texture",
                camera_node_path = GetOptionalArgument(Environment.GetCommandLineArgs(), "-i_mage_not_aiCameraNodePath") ?? "Camera.main",
                camera_distance_offset = Single.Parse(GetOptionalArgument(Environment.GetCommandLineArgs(), "-i_mage_not_aiCameraDistanceOffset") ?? "0", System.Globalization.CultureInfo.InvariantCulture),
                camera_zoom_scale = Single.Parse(GetOptionalArgument(Environment.GetCommandLineArgs(), "-i_mage_not_aiCameraZoomScale") ?? "1", System.Globalization.CultureInfo.InvariantCulture)
            },
            captures = captureRows.ToArray()
        };
        captureCamera.targetTexture = null;
        renderTarget.Release();
        Destroy(renderTarget);
        File.WriteAllText(outputPath, JsonUtility.ToJson(evidence, true));
        Debug.Log("I_MAGE_NOT_AI_RENDER_CAPTURE_PASS Unity frames=" + frameCount + " resolution=" + width + "x" + height);
#if UNITY_EDITOR
        UnityEditor.EditorApplication.Exit(0);
#else
        Application.Quit(0);
#endif
    }

    private static string[] DrainEvents()
    {
        var events = PendingEvents.ToArray();
        PendingEvents.Clear();
        return events;
    }

    private void Fail(string reason)
    {
        if (captureCamera != null) captureCamera.targetTexture = null;
        if (renderTarget != null)
        {
            renderTarget.Release();
            Destroy(renderTarget);
        }
        File.WriteAllText(outputPath + ".error", reason);
        Debug.LogError("I_MAGE_NOT_AI_RENDER_CAPTURE_FAIL " + reason);
#if UNITY_EDITOR
        UnityEditor.EditorApplication.Exit(1);
#else
        Application.Quit(1);
#endif
    }

    private static bool Contains(string[] arguments, string key)
    {
        for (var index = 0; index < arguments.Length; index++)
            if (arguments[index] == key && index + 1 < arguments.Length) return true;
        return false;
    }

    private static string GetArgument(string[] arguments, string key)
    {
        for (var index = 0; index < arguments.Length - 1; index++)
            if (arguments[index] == key) return arguments[index + 1];
        throw new InvalidOperationException("Missing Unity capture argument " + key);
    }

    private static string GetOptionalArgument(string[] arguments, string key)
    {
        for (var index = 0; index < arguments.Length - 1; index++)
            if (arguments[index] == key) return arguments[index + 1];
        return null;
    }
}
