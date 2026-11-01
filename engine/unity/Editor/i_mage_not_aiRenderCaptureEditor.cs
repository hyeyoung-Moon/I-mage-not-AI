using System;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class i_mage_not_aiRenderCaptureEditor
{
    private static string outputPath;
    private static double deadline;

    public static void RunFromCommandLine()
    {
        try
        {
            var args = Environment.GetCommandLineArgs();
            var projectRoot = Directory.GetCurrentDirectory();
            var scenePath = GetArgument(args, "-i_mage_not_aiScene");
            outputPath = GetArgument(args, "-i_mage_not_aiOutput");
            var absoluteScene = Path.GetFullPath(Path.Combine(projectRoot, scenePath));
            var assetsRoot = Path.GetFullPath(Path.Combine(projectRoot, "Assets")) + Path.DirectorySeparatorChar;
            if (!absoluteScene.StartsWith(assetsRoot, StringComparison.OrdinalIgnoreCase) || !File.Exists(absoluteScene) ||
                !String.Equals(Path.GetExtension(absoluteScene), ".unity", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Unity selection must be an existing scene inside Assets/.");
            if (File.Exists(outputPath) || File.Exists(outputPath + ".error"))
                throw new InvalidOperationException("Capture output already exists; choose a new evidence path.");
            EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);
            deadline = EditorApplication.timeSinceStartup + 240.0;
            EditorApplication.update += MonitorCapture;
            EditorApplication.isPlaying = true;
            Debug.Log("I_MAGE_NOT_AI_RENDER_CAPTURE_START Unity scene=" + scenePath + " output=" + outputPath);
        }
        catch (Exception exception)
        {
            File.WriteAllText(outputPath + ".error", exception.ToString());
            Debug.LogException(exception);
            EditorApplication.Exit(1);
        }
    }

    private static void MonitorCapture()
    {
        if (File.Exists(outputPath))
        {
            EditorApplication.update -= MonitorCapture;
            EditorApplication.Exit(0);
        }
        else if (File.Exists(outputPath + ".error") || EditorApplication.timeSinceStartup >= deadline)
        {
            EditorApplication.update -= MonitorCapture;
            EditorApplication.Exit(1);
        }
    }

    private static string GetArgument(string[] args, string key)
    {
        for (var index = 0; index < args.Length - 1; index++)
            if (args[index] == key) return args[index + 1];
        throw new InvalidOperationException("Missing Unity capture argument " + key);
    }
}
