using System;
using System.IO;
using System.Reflection;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class i_mage_not_aiIntegrationRunner
{
    public static void CreateRenderCaptureFixtureScene()
    {
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var cameraObject = new GameObject("Main Camera");
        cameraObject.tag = "MainCamera";
        cameraObject.transform.position = new Vector3(0f, 0f, -10f);
        var camera = cameraObject.AddComponent<Camera>();
        camera.orthographic = true;
        camera.orthographicSize = 2.2f;
        camera.clearFlags = CameraClearFlags.SolidColor;
        camera.backgroundColor = new Color(0.08f, 0.12f, 0.2f);
        var lightObject = new GameObject("Key Light");
        var light = lightObject.AddComponent<Light>();
        light.type = LightType.Directional;
        light.intensity = 2f;
        lightObject.transform.rotation = Quaternion.Euler(35f, -25f, 0f);
        var floor = GameObject.CreatePrimitive(PrimitiveType.Cube);
        floor.name = "Floor";
        floor.transform.position = new Vector3(0f, -1.15f, 0f);
        floor.transform.localScale = new Vector3(6f, 0.12f, 0.5f);
        floor.GetComponent<Renderer>().sharedMaterial = new Material(Shader.Find("Standard")) { color = new Color(0.45f, 0.8f, 0.64f) };
        var actor = GameObject.CreatePrimitive(PrimitiveType.Cube);
        actor.name = "Actor";
        actor.transform.position = new Vector3(-1.5f, -0.3f, 0f);
        actor.transform.localScale = new Vector3(0.55f, 1f, 0.5f);
        actor.GetComponent<Renderer>().sharedMaterial = new Material(Shader.Find("Standard")) { color = new Color(0.95f, 0.68f, 0.22f) };
        actor.AddComponent<i_mage_not_aiCaptureFixtureMover>();
        EditorSceneManager.SaveScene(scene, "Assets/i_mage_not_aiRenderCaptureFixture.unity");
    }

    public static void Run()
    {
        try
        {
            var menuMethod = typeof(i_mage_not_aiTraceExporter).GetMethod("ExportSelectedClip", BindingFlags.NonPublic | BindingFlags.Static);
            var menu = menuMethod == null ? null : (MenuItem)Attribute.GetCustomAttribute(menuMethod, typeof(MenuItem));
            if (menu == null || menu.menuItem != "Assets/Export AnimationClip as i_mage_not_ai Trace")
                throw new InvalidOperationException("Expected Unity Assets menu integration was not registered.");

            var clip = new AnimationClip { name = "fixture_walk", frameRate = 30f };
            string[] axes = { "x", "y", "z" };
            float[][] positions = { new[] { 0f, 1f }, new[] { 0f, 2f }, new[] { 0f, 3f } };
            for (int i = 0; i < 3; i++) AnimationUtility.SetEditorCurve(clip, EditorCurveBinding.FloatCurve("Animated", typeof(Transform), "m_LocalPosition." + axes[i]), AnimationCurve.Linear(0f, positions[i][0], 1f, positions[i][1]));
            float[][] rotations = { new[] { 0f, 0f }, new[] { 0f, 0.3826834f }, new[] { 0f, 0f }, new[] { 1f, 0.9238795f } };
            string[] qaxes = { "x", "y", "z", "w" };
            for (int i = 0; i < 4; i++) AnimationUtility.SetEditorCurve(clip, EditorCurveBinding.FloatCurve("Animated", typeof(Transform), "m_LocalRotation." + qaxes[i]), AnimationCurve.Linear(0f, rotations[i][0], 1f, rotations[i][1]));
            for (int i = 0; i < 3; i++) AnimationUtility.SetEditorCurve(clip, EditorCurveBinding.FloatCurve("Animated", typeof(Transform), "m_LocalScale." + axes[i]), AnimationCurve.Linear(0f, 1f, 1f, i == 1 ? 2f : 1f));
            AnimationUtility.SetAnimationEvents(clip, new[] { new AnimationEvent { time = 0.5f, functionName = "fixture_marker" } });
            const string clipPath = "Assets/fixture_walk.anim";
            if (AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath) != null) AssetDatabase.DeleteAsset(clipPath);
            AssetDatabase.CreateAsset(clip, clipPath);
            AssetDatabase.SaveAssets();
            var json = i_mage_not_aiTraceExporter.BuildExport(clip);
            if (!json.Contains("i_mage_not_ai.unity-export/1") || !json.Contains("fixture_marker") || !json.Contains("fixture_walk::Animated") || !json.Contains("fixture_walk::Animated::rotation") || !json.Contains("\"kind\":\"rotation\""))
                throw new InvalidOperationException("Unity trace output did not contain the expected version, position, rotation, and event data.");
            File.WriteAllText(Path.GetFullPath("i_mage_not_ai-unity-export.json"), json);
            File.WriteAllText(Path.GetFullPath("i_mage_not_ai-unity-editor-menu.txt"), menu.menuItem);

            SessionState.SetBool(i_mage_not_aiRuntimePlayModeProbe.SessionKey, true);
            EditorApplication.isPlaying = true;
        }
        catch (Exception ex)
        {
            File.WriteAllText(Path.GetFullPath("i_mage_not_ai-unity-integration-error.txt"), ex.ToString());
            Debug.LogException(ex);
            EditorApplication.Exit(1);
        }
    }

    public static void RunRuntimeProbe()
    {
        const int seed = 481516;
        var callbackObject = new GameObject("RuntimeCallback");
        var callback = callbackObject.AddComponent<i_mage_not_aiRuntimeCallback>();
        var runtimeClip = new AnimationClip { name = "runtime_callback", frameRate = 60f };
        runtimeClip.SetCurve("", typeof(Transform), "localPosition.x", AnimationCurve.Linear(0f, 0f, 1f, 1f));
        runtimeClip.AddEvent(new AnimationEvent { time = 0.5f, functionName = "FixtureMarker" });
        const string controllerPath = "Assets/runtime_probe.controller";
        var oldController = AssetDatabase.LoadAssetAtPath<AnimatorController>(controllerPath);
        if (oldController != null) AssetDatabase.DeleteAsset(controllerPath);
        var controller = AnimatorController.CreateAnimatorControllerAtPath(controllerPath);
        var state = controller.AddMotion(runtimeClip);
        var animator = callbackObject.AddComponent<Animator>();
        animator.runtimeAnimatorController = controller;
        animator.Rebind();
        animator.Play(state.name, 0, 0f);
        animator.Update(0f);
        animator.Update(0.5f);
        animator.Update(0.001f);
        if (callback.CallbackTimes.Count != 1 || Mathf.Abs(callback.CallbackTimes[0] - 0.5f) > 1e-5f)
            throw new InvalidOperationException("Animator did not invoke FixtureMarker exactly once at 0.5 seconds; observed " + callback.CallbackTimes.Count + " calls.");

        var particleObject = new GameObject("RuntimeParticles");
        var system = particleObject.AddComponent<ParticleSystem>();
        system.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
        var main = system.main;
        main.duration = 1f;
        main.loop = false;
        main.startLifetime = 1f;
        main.startSpeed = 2f;
        main.startSize = 0.1f;
        main.simulationSpace = ParticleSystemSimulationSpace.Local;
        main.useUnscaledTime = false;
        main.playOnAwake = false;
        var emission = system.emission;
        emission.enabled = false;
        var shape = system.shape;
        shape.enabled = true;
        shape.shapeType = ParticleSystemShapeType.Cone;
        shape.angle = 0f;
        shape.radius = 0f;
        var velocity = system.velocityOverLifetime;
        velocity.enabled = false;
        var force = system.forceOverLifetime;
        force.enabled = false;
        var collision = system.collision;
        collision.enabled = false;
        var subEmitters = system.subEmitters;
        subEmitters.enabled = false;
        system.useAutoRandomSeed = false;
        system.randomSeed = seed;
        Time.captureDeltaTime = 1f / 60f;
        system.Play();
        system.Emit(8);
        var waiterObject = new GameObject("RuntimeWaiter");
        var waiter = waiterObject.AddComponent<i_mage_not_aiRuntimeWaiter>();
        waiter.Begin(callback, system, callbackObject, particleObject, controllerPath, seed);
    }
}

[InitializeOnLoad]
public static class i_mage_not_aiRuntimePlayModeProbe
{
    public const string SessionKey = "i_mage_not_ai.runtime_probe.pending";

    static i_mage_not_aiRuntimePlayModeProbe()
    {
        EditorApplication.playModeStateChanged += OnPlayModeStateChanged;
    }

    private static void OnPlayModeStateChanged(PlayModeStateChange state)
    {
        if (state != PlayModeStateChange.EnteredPlayMode || !SessionState.GetBool(SessionKey, false)) return;
        SessionState.SetBool(SessionKey, false);
        try
        {
            i_mage_not_aiIntegrationRunner.RunRuntimeProbe();
            EditorApplication.update += CheckRuntimeResult;
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            EditorApplication.Exit(1);
        }
    }

    private static void CheckRuntimeResult()
    {
        if (!i_mage_not_aiRuntimeWaiter.Completed) return;
        EditorApplication.update -= CheckRuntimeResult;
        if (!String.IsNullOrEmpty(i_mage_not_aiRuntimeWaiter.Failure))
        {
            Debug.LogError("I_MAGE_NOT_AI_RUNTIME_FAIL " + i_mage_not_aiRuntimeWaiter.Failure);
            EditorApplication.Exit(1);
            return;
        }
        Debug.Log("I_MAGE_NOT_AI_INTEGRATION_PASS Unity " + Application.unityVersion + " callbacks=1 particles=8");
        AssetDatabase.DeleteAsset("Assets/runtime_probe.controller");
        EditorApplication.Exit(0);
    }
}
