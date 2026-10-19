-- Made by sleep_editor.py for episode: example
local JOB = {
    name = [==[example]==],
    fcpxml = [==[C:\path\to\longform-video-pipeline\episodes\example\example.fcpxml]==],
    render_dir = [==[C:\path\to\longform-video-pipeline\episodes\example\render]==],
    width = 2560, height = 1440, fps = 30,
    codec = [==[H264]==],
    video_bitrate_kbps = 20000,
    auto_render = false,
}

local rv = resolve or (Resolve and Resolve()) or bmd.scriptapp("Resolve")

local function start_render(project, name)
    rv:OpenPage("deliver")
    project:SetCurrentRenderMode(1)
    if not project:SetCurrentRenderFormatAndCodec("mp4", JOB.codec) then
        print("Couldn't set MP4/H.264 - check the Deliver page settings.")
    end
    local settings = {
        SelectAllFrames = true,
        TargetDir = JOB.render_dir,
        CustomName = name,
        FormatWidth = JOB.width,
        FormatHeight = JOB.height,
        ExportVideo = true,
        ExportAudio = true,
    }
    if JOB.video_bitrate_kbps > 0 then settings.VideoQuality = JOB.video_bitrate_kbps end
    project:SetRenderSettings(settings)
    local id = project:AddRenderJob()
    if not id then
        print("Couldn't add the render job.")
        return
    end
    project:StartRendering(id)
    print("Rendering '" .. name .. "' at " .. JOB.width .. "x" .. JOB.height .. " -> " .. JOB.render_dir)
end

local project = rv:GetProjectManager():GetCurrentProject()
local tl = project and project:GetCurrentTimeline()
if not tl then
    print("Open the project and timeline you want to render first.")
else
    start_render(project, tl:GetName())
end
