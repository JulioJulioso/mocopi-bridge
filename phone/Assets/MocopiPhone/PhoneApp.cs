using System;
using System.Collections.Generic;
using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Text;
using UnityEngine;

/// <summary>
/// Phone screen for free mocopi sensors. Each sensor is hip, an ankle, or a named object.
/// The official Sony body app must not be connected to the same sensors.
/// </summary>
public sealed class PhoneApp : MonoBehaviour
{
    public static PhoneApp Instance { get; private set; }

    const int Line = 28;
    readonly List<LiveSensor> sensors = new List<LiveSensor>();
    readonly Dictionary<string, string> roles = new Dictionary<string, string>();
    readonly Dictionary<string, string> objectNames = new Dictionary<string, string>();
    string pcAddress = "192.168.1.10";
    string portText = "12351";
    bool sending = true;
    bool simulate = true;
    string status = "Assign a role to each sensor, then send.";
    float nextSend;
    UdpClient client;

    void Awake()
    {
        if (Instance != null && Instance != this)
        {
            Destroy(gameObject);
            return;
        }
        Instance = this;
        DontDestroyOnLoad(gameObject);
        client = new UdpClient();
        if (sensors.Count == 0)
            SeedSimulation();
    }

    void OnDestroy()
    {
        if (Instance == this)
            Instance = null;
        client?.Close();
    }

    /// <summary>
    /// Call this from Sony's sensor data reader callback after you import that package.
    /// The package itself stays out of source control.
    /// </summary>
    public void PushFromSdk(string id, Quaternion rotation, Vector3 acceleration, float battery01)
    {
        simulate = false;
        Upsert(id, rotation, acceleration, battery01, true);
    }

    void Update()
    {
        if (simulate)
            AnimateSimulation();
        if (!sending || Time.unscaledTime < nextSend)
            return;
        nextSend = Time.unscaledTime + 0.05f;
        if (!int.TryParse(portText, out int port))
        {
            status = "Port must be a number.";
            return;
        }
        foreach (LiveSensor sensor in sensors)
        {
            if (!sensor.Connected || !roles.TryGetValue(sensor.Id, out string role) || role == "unused")
                continue;
            string name = objectNames.TryGetValue(sensor.Id, out string objectName) ? objectName : "box";
            Send(SampleJson(sensor, role, name), port);
        }
    }

    void OnGUI()
    {
        GUILayout.BeginArea(new Rect(16, 16, Screen.width - 32, Screen.height - 32));
        GUILayout.Label("mocopi sensors");
        GUILayout.Label("This PC address and port. Use the same Wi-Fi. Do not also open Sony's mocopi app.");
        GUILayout.BeginHorizontal();
        pcAddress = GUILayout.TextField(pcAddress, GUILayout.Width(220));
        portText = GUILayout.TextField(portText, GUILayout.Width(80));
        sending = GUILayout.Toggle(sending, "Send");
        GUILayout.EndHorizontal();

        foreach (LiveSensor sensor in sensors)
        {
            GUILayout.BeginHorizontal();
            string battery = sensor.Battery < 0f ? "" : "  " + MathfRound(sensor.Battery * 100f) + "%";
            GUILayout.Label(sensor.Id + battery, GUILayout.Width(180));
            string role = RoleOf(sensor.Id);
            if (GUILayout.Button(RoleLabel(role), GUILayout.Width(140)))
                roles[sensor.Id] = NextRole(role);
            if (RoleOf(sensor.Id) == "prop")
                objectNames[sensor.Id] = GUILayout.TextField(ObjectName(sensor.Id), GUILayout.Width(120));
            GUILayout.EndHorizontal();
        }

        if (GUILayout.Button("Reset object orientation"))
            SendResets();
        GUILayout.Label(status);
        GUILayout.EndArea();
    }

    void SendResets()
    {
        if (!int.TryParse(portText, out int port))
            return;
        var sent = new HashSet<string>();
        foreach (LiveSensor sensor in sensors)
        {
            if (RoleOf(sensor.Id) != "prop")
                continue;
            string name = ObjectName(sensor.Id);
            if (!sent.Add(name))
                continue;
            Send("{\"v\":1,\"type\":\"reset\",\"role\":\"prop\",\"name\":\"" + Escape(name) + "\"}", port);
        }
        status = sent.Count == 0 ? "Assign an object before reset." : "Reset sent for " + string.Join(", ", sent) + ".";
    }

    void Send(string json, int port)
    {
        try
        {
            byte[] bytes = Encoding.UTF8.GetBytes(json);
            client.Send(bytes, bytes.Length, new IPEndPoint(IPAddress.Parse(pcAddress), port));
        }
        catch (Exception error)
        {
            status = error.Message;
        }
    }

    static string SampleJson(LiveSensor sensor, string role, string name)
    {
        var culture = CultureInfo.InvariantCulture;
        Quaternion q = sensor.Rotation;
        Vector3 a = sensor.Acceleration;
        return "{\"v\":1,\"type\":\"sensor\",\"id\":\"" + Escape(sensor.Id) +
               "\",\"role\":\"" + role + "\",\"name\":\"" + Escape(role == "prop" ? name : "") +
               "\",\"q\":[" + q.x.ToString(culture) + "," + q.y.ToString(culture) + "," +
               q.z.ToString(culture) + "," + q.w.ToString(culture) +
               "],\"a\":[" + a.x.ToString(culture) + "," + a.y.ToString(culture) + "," +
               a.z.ToString(culture) + "]}";
    }

    void SeedSimulation()
    {
        Upsert("hip-sim", Quaternion.identity, new Vector3(0f, 9.8f, 0f), 0.8f, true);
        Upsert("left-ankle-sim", Quaternion.identity, new Vector3(0f, 9.8f, 0f), 0.8f, true);
        Upsert("right-ankle-sim", Quaternion.identity, new Vector3(0f, 9.8f, 0f), 0.8f, true);
        Upsert("box-sim", Quaternion.identity, Vector3.zero, 0.9f, true);
        roles["hip-sim"] = "hip";
        roles["left-ankle-sim"] = "left_ankle";
        roles["right-ankle-sim"] = "right_ankle";
        roles["box-sim"] = "prop";
        objectNames["box-sim"] = "box";
    }

    void AnimateSimulation()
    {
        float turn = Time.unscaledTime * 20f;
        foreach (LiveSensor sensor in sensors)
        {
            if (!sensor.Id.EndsWith("-sim", StringComparison.Ordinal))
                continue;
            if (sensor.Id == "box-sim")
                sensor.Rotation = Quaternion.Euler(0f, turn, 0f);
            else if (sensor.Id == "hip-sim")
                sensor.Rotation = Quaternion.Euler(0f, Mathf.Sin(Time.unscaledTime) * 15f, 0f);
        }
    }

    void Upsert(string id, Quaternion rotation, Vector3 acceleration, float battery, bool connected)
    {
        LiveSensor found = sensors.Find(item => item.Id == id);
        if (found == null)
        {
            found = new LiveSensor { Id = id };
            sensors.Add(found);
            roles[id] = "unused";
        }
        found.Rotation = rotation;
        found.Acceleration = acceleration;
        found.Battery = battery;
        found.Connected = connected;
    }

    string RoleOf(string id)
    {
        return roles.TryGetValue(id, out string role) ? role : "unused";
    }

    string ObjectName(string id)
    {
        return objectNames.TryGetValue(id, out string name) && name.Length > 0 ? name : "box";
    }

    static string NextRole(string role)
    {
        switch (role)
        {
            case "unused": return "hip";
            case "hip": return "left_ankle";
            case "left_ankle": return "right_ankle";
            case "right_ankle": return "prop";
            default: return "unused";
        }
    }

    static string RoleLabel(string role)
    {
        switch (role)
        {
            case "hip": return "Hip";
            case "left_ankle": return "Left ankle";
            case "right_ankle": return "Right ankle";
            case "prop": return "Object";
            default: return "Unused";
        }
    }

    static string Escape(string value)
    {
        return (value ?? "").Replace("\\", "\\\\").Replace("\"", "");
    }

    static int MathfRound(float value)
    {
        return Mathf.RoundToInt(value);
    }

    sealed class LiveSensor
    {
        public string Id;
        public Quaternion Rotation;
        public Vector3 Acceleration;
        public float Battery = -1f;
        public bool Connected = true;
    }
}
