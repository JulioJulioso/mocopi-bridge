using System;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEngine;

/// <summary>
/// Turns a scene object with the packet's rotation. Position stays where you placed the model.
/// A reset from the phone makes the current sensor attitude the zero rotation.
/// </summary>
public sealed class PropReceiver : MonoBehaviour
{
    public int port = 12354;
    UdpClient client;
    Thread thread;
    volatile bool running;
    readonly object gate = new object();
    string pendingName;
    Quaternion pendingRotation = Quaternion.identity;
    bool hasPacket;

    void Start()
    {
        client = new UdpClient(port);
        running = true;
        thread = new Thread(Listen) { IsBackground = true };
        thread.Start();
    }

    void OnDestroy()
    {
        running = false;
        client?.Close();
    }

    void Update()
    {
        string name;
        Quaternion rotation;
        lock (gate)
        {
            if (!hasPacket)
                return;
            name = pendingName;
            rotation = pendingRotation;
            hasPacket = false;
        }
        Transform target = FindNamed(name);
        if (target == null)
            return;
        target.rotation = rotation;
    }

    void Listen()
    {
        var any = new IPEndPoint(IPAddress.Any, 0);
        while (running)
        {
            try
            {
                byte[] data = client.Receive(ref any);
                if (!TryRead(Encoding.UTF8.GetString(data), out string name, out Quaternion rotation))
                    continue;
                lock (gate)
                {
                    pendingName = name;
                    pendingRotation = rotation;
                    hasPacket = true;
                }
            }
            catch (SocketException)
            {
                if (!running)
                    return;
            }
            catch (ObjectDisposedException)
            {
                return;
            }
        }
    }

    static bool TryRead(string json, out string name, out Quaternion rotation)
    {
        name = "";
        rotation = Quaternion.identity;
        if (json.IndexOf("\"type\":\"prop\"", StringComparison.Ordinal) < 0)
            return false;
        name = Between(json, "\"name\":\"", "\"");
        string numbers = Between(json, "\"q\":[", "]");
        string[] parts = numbers.Split(',');
        if (name.Length == 0 || parts.Length != 4)
            return false;
        if (!float.TryParse(parts[0], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out float x))
            return false;
        if (!float.TryParse(parts[1], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out float y))
            return false;
        if (!float.TryParse(parts[2], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out float z))
            return false;
        if (!float.TryParse(parts[3], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out float w))
            return false;
        rotation = new Quaternion(x, y, z, w);
        return true;
    }

    static string Between(string json, string start, string end)
    {
        int from = json.IndexOf(start, StringComparison.Ordinal);
        if (from < 0)
            return "";
        from += start.Length;
        int to = json.IndexOf(end, from, StringComparison.Ordinal);
        if (to < 0)
            return "";
        return json.Substring(from, to - from);
    }

    static Transform FindNamed(string objectName)
    {
        GameObject[] objects = FindObjectsOfType<GameObject>();
        for (int i = 0; i < objects.Length; i++)
        {
            if (objects[i].name == objectName)
                return objects[i].transform;
        }
        return null;
    }
}
