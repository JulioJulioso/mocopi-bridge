using UnityEngine;

/// <summary>
/// Play this project to see a box that follows a prop sensor named "box".
/// Put your own model in the scene and name it the same as the object on the phone.
/// The script does not move the object, it only turns it.
/// </summary>
public static class PropScene
{
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Start()
    {
        if (GameObject.Find("box") == null)
        {
            GameObject box = GameObject.CreatePrimitive(PrimitiveType.Cube);
            box.name = "box";
            box.transform.position = new Vector3(0f, 0.5f, 1.5f);
        }
        if (Object.FindObjectOfType<PropReceiver>() == null)
        {
            var listener = new GameObject("prop listener");
            listener.AddComponent<PropReceiver>();
        }
    }
}
