using UnityEngine;

/// <summary>
/// Creates the phone screen when you press Play. No scene setup is required.
/// </summary>
public static class PhoneBootstrap
{
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Start()
    {
        if (PhoneApp.Instance != null)
            return;
        var host = new GameObject("mocopi phone");
        host.AddComponent<PhoneApp>();
    }
}
