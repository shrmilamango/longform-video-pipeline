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

-- colour correction per clip: { slope, offset, power, saturation }
local CDL = {
    [ [==[pixabay_100713]==] ] = { [==[1.199 1.236 1.285]==], [==[-0.008 -0.008 -0.008]==], [==[1.060 1.060 1.060]==], [==[0.920]==] },
    [ [==[pixabay_100713.mp4]==] ] = { [==[1.199 1.236 1.285]==], [==[-0.008 -0.008 -0.008]==], [==[1.060 1.060 1.060]==], [==[0.920]==] },
    [ [==[pixabay_100771]==] ] = { [==[0.970 1.000 1.040]==], [==[-0.008 -0.008 -0.008]==], [==[1.060 1.060 1.060]==], [==[0.920]==] },
    [ [==[pixabay_100771.mp4]==] ] = { [==[0.970 1.000 1.040]==], [==[-0.008 -0.008 -0.008]==], [==[1.060 1.060 1.060]==], [==[0.920]==] },
    [ [==[pixabay_100889]==] ] = { [==[0.727 0.750 0.780]==], [==[-0.008 -0.008 -0.008]==], [==[1.060 1.060 1.060]==], [==[0.920]==] },
    -- ... (one line per clip; the real file has 820)
}

local function find_cdl(item)
    local names = { item:GetName() or "" }
    local mpi = item:GetMediaPoolItem()
    if mpi then names[#names + 1] = mpi:GetName() or "" end
    for _, n in ipairs(names) do
        local c = CDL[n] or CDL[(n:gsub("%.%w+$", ""))]
        if c then return c end
    end
    return nil
end

local function apply_color(tl)
    local found, done = 0, 0
    rv:OpenPage("color")
    for _, item in pairs(tl:GetItemListInTrack("video", 1) or {}) do
        local c = find_cdl(item)
        if c then
            found = found + 1
            local ok = item:SetCDL({ NodeIndex = "1", Slope = c[1], Offset = c[2], Power = c[3], Saturation = c[4] })
            if ok then done = done + 1 end
        end
    end
    rv:OpenPage("edit")
    if found == 0 then return end
    print("Colour correction applied to " .. done .. " of " .. found .. " clips.")
    if done == 0 then
        print("(Resolve didn't accept the colour settings - you can grade on the Color page instead.)")
    end
end

local function build()
print("SleepEditor: building '" .. JOB.name .. "' ... (big timelines take 1-2 minutes)")
print("  timeline file: " .. JOB.fcpxml)
local pm = rv:GetProjectManager()
local project = pm:CreateProject(JOB.name)
local n = 2
while not project and n < 50 do
    project = pm:CreateProject(JOB.name .. "_" .. n)
    n = n + 1
end
if not project then
    print("Couldn't create a new project.")
else
    project:SetSetting("timelineResolutionWidth", tostring(JOB.width))
    project:SetSetting("timelineResolutionHeight", tostring(JOB.height))
    project:SetSetting("timelineFrameRate", tostring(JOB.fps))
    project:SetSetting("timelinePlaybackFrameRate", tostring(JOB.fps))
    local mp = project:GetMediaPool()
    local tl = mp:ImportTimelineFromFile(JOB.fcpxml, { timelineName = JOB.name, importSourceClips = true })
    if not tl then
        print("Timeline import failed. Try File > Import > Timeline and pick:")
        print("  " .. JOB.fcpxml)
    else
        project:SetCurrentTimeline(tl)
        local count = 0
        for _ in pairs(tl:GetItemListInTrack("video", 1) or {}) do count = count + 1 end
        print("Timeline '" .. JOB.name .. "' ready: " .. count .. " clips.")
        apply_color(tl)
        rv:OpenPage("edit")
        pm:SaveProject()
        if JOB.auto_render then
            start_render(project, JOB.name)
        else
            print("Check the timeline, then run Workspace > Scripts > SleepEditor_Render.")
        end
    end
end
end

local ok, err = pcall(build)
if not ok then
    print("SleepEditor ERROR: " .. tostring(err))
    print("Send a screenshot of this Console to Claude.")
end
