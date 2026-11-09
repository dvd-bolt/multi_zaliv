using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("FrameFlow")]
[assembly: AssemblyProduct("FrameFlow")]
[assembly: AssemblyCompany("FrameFlow")]
[assembly: AssemblyVersion("0.2.0.0")]
[assembly: AssemblyFileVersion("0.2.0.0")]

internal static class Launcher {
    private static string Quote(string value) {
        var result = new System.Text.StringBuilder("\"");
        int slashes = 0;
        foreach (char character in value) {
            if (character == '\\') { slashes++; continue; }
            result.Append('\\', slashes * (character == '"' ? 2 : 1));
            if (character == '"') result.Append('\\');
            result.Append(character);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        result.Append('"');
        return result.ToString();
    }

    [STAThread]
    private static int Main(string[] args) {
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(root, "runtime", "pythonw.exe");
            string entry = Path.Combine(root, "scripts", "installed_entry.py");
            if (!File.Exists(python) || !File.Exists(entry))
                throw new Exception("Файлы FrameFlow не найдены. Повторите установку программы.");
            var info = new ProcessStartInfo(python, "-B " + Quote(entry));
            foreach (string argument in args) info.Arguments += " " + Quote(argument);
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WindowStyle = ProcessWindowStyle.Hidden;
            Process child = Process.Start(info);
            child.WaitForExit();
            return child.ExitCode;
        } catch (Exception error) {
            MessageBox.Show(error.Message, "FrameFlow — запуск", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
