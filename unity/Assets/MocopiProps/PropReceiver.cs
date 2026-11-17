using System;
using System.Collections.Generic;
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
    // Latest rotation per object. Several props share one socket, so one slot would drop all but the last.
    readonly Dictionary<string, Quaternion> pending = new Dictionary<string, Quaternion>();
    readonly List<KeyValuePair<string, Quaternion>> drained = new List<KeyValuePair<string, Quaternion>>();
    readonly Dictionary<string, Transform> targets = new Dictionary<string, Transform>();

    void Start()
    {
        try
        {
            client = new UdpClient(port);
        }
        catch (SocketException error)
        {
            Debug.LogError("PropReceiver cannot listen on UDP " + port + ": " + error.Message);
            enabled = false;
            return;
        }
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
        lock (gate)
        {
            if (pending.Count == 0)
                return;
            drained.Clear();
            drained.AddRange(pending);
            pending.Clear();
        }
        for (int i = 0; i < drained.Count; i++)
        {
            Transform target = FindNamed(drained[i].Key);
            if (target != null)
                target.rotation = drained[i].Value;
        }
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
                    pending[name] = rotation;
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
        var style = System.Globalization.NumberStyles.Float;
        var culture = System.Globalization.CultureInfo.InvariantCulture;
        if (!float.TryParse(parts[0], style, culture, out float x))
            return false;
        if (!float.TryParse(parts[1], style, culture, out float y))
            return false;
        if (!float.TryParse(parts[2], style, culture, out float z))
            return false;
        if (!float.TryParse(parts[3], style, culture, out float w))
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

    Transform FindNamed(string objectName)
    {
        // A destroyed Transform compares equal to null, so a stale entry is looked up again.
        if (targets.TryGetValue(objectName, out Transform cached) && cached != null)
            return cached;
        GameObject found = GameObject.Find(objectName);
        Transform target = found != null ? found.transform : null;
        if (target != null)
            targets[objectName] = target;
        else
            targets.Remove(objectName);
        return target;
    }
}
