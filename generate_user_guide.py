"""Generate instruction sheet PDF for the Optical CT dosimetry app."""
import sys
sys.path.insert(0, '/Users/E119562/Library/Python/3.9/lib/python/site-packages')

from fpdf import FPDF, XPos, YPos

# ── Colour palette ─────────────────────────────────────────────────────────────
C_BG       = (255, 255, 255)
C_HEADER   = (15,  90,  80)   # deep teal
C_SUBHEAD  = (30, 140, 120)   # mid teal
C_ACCENT   = (0,  180, 140)   # bright teal
C_TEXT     = (30,  35,  45)   # near-black
C_DIM      = (100, 110, 130)  # grey
C_NOTE_BG  = (235, 248, 245)  # very light teal
C_WARN_BG  = (255, 248, 230)  # light amber
C_STEP_BG  = (245, 247, 252)  # very light blue-grey
C_RULE     = (200, 220, 215)  # light teal rule

MARGIN = 18
PAGE_W = 210
CONTENT_W = PAGE_W - 2 * MARGIN


FONT_DIR = '/System/Library/Fonts/Supplemental'

class Guide(FPDF):
    def __init__(self):
        super().__init__('P', 'mm', 'A4')
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self._section_num = 0
        # Register Arial as a Unicode-capable font family
        self.add_font('Arial', '',  f'{FONT_DIR}/Arial.ttf')
        self.add_font('Arial', 'B', f'{FONT_DIR}/Arial Bold.ttf')
        self.add_font('Arial', 'I', f'{FONT_DIR}/Arial Italic.ttf')
        self.add_font('Arial', 'BI', f'{FONT_DIR}/Arial Bold Italic.ttf')

    # ── Helpers ────────────────────────────────────────────────────────────────

    def rule(self, color=C_RULE, thickness=0.3):
        self.set_draw_color(*color)
        self.set_line_width(thickness)
        self.line(MARGIN, self.get_y(), PAGE_W - MARGIN, self.get_y())
        self.ln(2)

    def section(self, title):
        self._section_num += 1
        self.ln(4)
        self.set_fill_color(*C_HEADER)
        self.set_text_color(255, 255, 255)
        self.set_font('Arial', 'B', 11)
        self.cell(CONTENT_W, 7, f'  {self._section_num}.  {title}',
                  fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)
        self.set_text_color(*C_TEXT)

    def subsection(self, title):
        self.ln(2)
        self.set_text_color(*C_SUBHEAD)
        self.set_font('Arial', 'B', 10)
        self.cell(CONTENT_W, 6, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*C_TEXT)
        self.rule(C_RULE, 0.2)

    def body(self, text, indent=0):
        self.set_font('Arial', '', 9.5)
        self.set_text_color(*C_TEXT)
        self.set_x(MARGIN + indent)
        self.multi_cell(CONTENT_W - indent, 5.5, text,
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def step(self, num, bold_part, rest=''):
        """Numbered step with optional bold lead-in."""
        self.set_fill_color(*C_STEP_BG)
        x0 = MARGIN
        y0 = self.get_y()
        # number badge
        self.set_xy(x0, y0)
        self.set_font('Arial', 'B', 9)
        self.set_text_color(*C_HEADER)
        self.cell(7, 6, str(num), align='C')
        # bold part
        self.set_font('Arial', 'B', 9.5)
        self.set_text_color(*C_TEXT)
        self.set_x(x0 + 8)
        self.cell(0, 6, bold_part, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if rest:
            self.set_font('Arial', '', 9)
            self.set_text_color(*C_DIM)
            self.set_x(x0 + 8)
            self.multi_cell(CONTENT_W - 8, 5, rest,
                            new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*C_TEXT)
        self.ln(0.5)

    def note(self, text, bg=C_NOTE_BG, label='Note'):
        self.ln(1)
        self.set_fill_color(*bg)
        self.set_x(MARGIN)
        self.set_font('Arial', 'B', 8.5)
        self.set_text_color(*C_HEADER)
        self.cell(CONTENT_W, 5, f'  {label}', fill=True,
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font('Arial', '', 8.5)
        self.set_text_color(*C_TEXT)
        self.set_x(MARGIN)
        self.multi_cell(CONTENT_W, 5.2, f'  {text}', fill=True,
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def warning(self, text):
        self.note(text, bg=C_WARN_BG, label='! Warning')

    def ui(self, label):
        """Inline reference to a UI element — returns formatted string."""
        return f'"{label}"'

    def param_row(self, name, default, description):
        self.set_font('Arial', 'B', 9)
        self.set_text_color(*C_SUBHEAD)
        self.set_x(MARGIN)
        self.cell(42, 5.5, name)
        self.set_font('Arial', 'I', 9)
        self.set_text_color(*C_DIM)
        self.cell(22, 5.5, f'default: {default}')
        self.set_font('Arial', '', 9)
        self.set_text_color(*C_TEXT)
        self.multi_cell(CONTENT_W - 64, 5.5, description,
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # ── Header / Footer ────────────────────────────────────────────────────────

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font('Arial', 'I', 8)
        self.set_text_color(*C_DIM)
        self.cell(0, 8, 'Optical CT Dosimetry App — User Guide', align='L')
        self.cell(0, 8, f'Page {self.page_no()}', align='R',
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.rule()

    def footer(self):
        self.set_y(-14)
        self.set_font('Arial', 'I', 7.5)
        self.set_text_color(*C_DIM)
        self.rule()
        self.cell(0, 5, 'For research use only. Not for clinical decision-making.',
                  align='C')


# ══════════════════════════════════════════════════════════════════════════════
# Build document
# ══════════════════════════════════════════════════════════════════════════════

pdf = Guide()

# ── Page 1: Cover ─────────────────────────────────────────────────────────────
pdf.add_page()

pdf.set_fill_color(*C_HEADER)
pdf.rect(0, 0, 210, 70, 'F')

ICON = '/Users/E119562/repos/Optical-CT/optical_ct_app_icon.png'
ICON_W = 30   # mm
ICON_X = (PAGE_W - ICON_W) / 2
pdf.image(ICON, x=ICON_X, y=6, w=ICON_W)

pdf.set_y(40)
pdf.set_font('Arial', 'B', 22)
pdf.set_text_color(255, 255, 255)
pdf.cell(0, 12, 'Optical CT Dosimetry', align='C',
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.set_font('Arial', '', 13)
pdf.cell(0, 8, 'Step-by-step user guide', align='C',
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.set_font('Arial', 'I', 9)
pdf.set_text_color(180, 230, 220)
pdf.cell(0, 6, 'For medical physics students and researchers', align='C',
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)

pdf.set_y(76)
pdf.set_text_color(*C_TEXT)

pdf.set_font('Arial', 'B', 10)
pdf.set_text_color(*C_SUBHEAD)
pdf.cell(0, 7, 'What this guide covers', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.rule()

pdf.body(
    'This guide walks you through every step of a complete optical CT dosimetry measurement: '
    'from initial hardware setup and calibration, through the pre-irradiation scan, '
    'irradiation of the dosimeter, post-irradiation scan, 3-D reconstruction, and '
    'final depth dose extraction. Each procedure is described in the order you will '
    'perform it in the app.'
)

pdf.ln(3)

# Quick-reference box
pdf.set_fill_color(*C_NOTE_BG)
pdf.set_draw_color(*C_ACCENT)
pdf.set_line_width(0.4)
pdf.set_x(MARGIN)
pdf.set_font('Arial', 'B', 9.5)
pdf.set_text_color(*C_HEADER)
pdf.cell(CONTENT_W, 6, '  Workflow at a glance', fill=True,
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)

steps_glance = [
    ('Day 1',         'Pre-irradiation scan',
                      'Capture dark, flat, and pre-irradiation projections.'),
    ('Day 1–N',       'Irradiation',
                      'Deliver the planned dose to the dosimeter (outside this app).'),
    ('Day N',         'Post-irradiation scan',
                      'Capture dark, flat, and post-irradiation projections.  '
                      'ΔA projections are computed automatically.'),
    ('After scan',    'Reconstruction',
                      'Run FBP reconstruction to obtain the 3-D attenuation volume.'),
    ('After recon',   'Depth dose',
                      'Extract relative dose vs. depth and export results.'),
]
for tag, title, desc in steps_glance:
    pdf.set_fill_color(*C_NOTE_BG)
    pdf.set_x(MARGIN)
    pdf.set_font('Arial', 'B', 9)
    pdf.set_text_color(*C_DIM)
    pdf.cell(22, 5.5, tag, fill=True)
    pdf.set_font('Arial', 'B', 9)
    pdf.set_text_color(*C_SUBHEAD)
    pdf.cell(40, 5.5, title, fill=True)
    pdf.set_font('Arial', '', 9)
    pdf.set_text_color(*C_TEXT)
    pdf.multi_cell(CONTENT_W - 62, 5.5, desc, fill=True,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)

pdf.ln(4)

# Physics background
pdf.section('Background: How optical CT dosimetry works')

pdf.body(
    'Optical CT dosimetry uses a radiochromic dosimeter, a solid urethane cylinder '
    'dyed throughout, that darkens '
    'proportionally to the absorbed radiation dose.  The app quantifies this darkening '
    'by measuring the change in optical density (ΔA) between a pre-irradiation and a '
    'post-irradiation scan.'
)
pdf.ln(1)
pdf.body(
    'For each projection angle, the Beer–Lambert law relates measured pixel intensity I '
    'to absorbance A:',
    indent=4
)
pdf.set_font('Arial', 'BI', 9.5)
pdf.set_text_color(*C_SUBHEAD)
pdf.set_x(MARGIN + 12)
pdf.cell(0, 6, 'A = −log(I / I0)',
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.set_text_color(*C_TEXT)
pdf.body(
    'where I0 is the flat-field (lamp-only) intensity.  The dose-related signal is the '
    'difference:',
    indent=4
)
pdf.set_font('Arial', 'BI', 9.5)
pdf.set_text_color(*C_SUBHEAD)
pdf.set_x(MARGIN + 12)
pdf.cell(0, 6, 'ΔA = A_post − A_pre = log(I_pre / I_post)',
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.set_text_color(*C_TEXT)
pdf.body(
    'Note that I0 cancels exactly, making the measurement independent of '
    'lamp-intensity drift between sessions.  A set of ΔA projections at evenly-spaced '
    'angles is then reconstructed with filtered back-projection (FBP) to yield a '
    '3-D attenuation volume, from which a depth dose profile is extracted.'
)

pdf.note(
    'Dark frames (lamp OFF) are subtracted before every calculation to remove '
    'camera read-out noise and hot pixels.  Always capture fresh dark and flat frames '
    'at the start of each session.'
)

# ── Page 2: Hardware & first launch ──────────────────────────────────────────
pdf.add_page()

pdf.section('Hardware setup and first launch')

pdf.subsection('Starting the app')
pdf.step(1, 'Launch the app.',
         'Double-click the app icon or run:  python3 oct_app.py')
pdf.step(2, 'The workflow dialog appears.',
         'Choose the appropriate workflow for your current session (see Section 3).')
pdf.step(3, 'Check "Real camera" and "Real stepper" checkboxes in the Scan panel.',
         'Leave both unchecked only for off-line testing with the camera simulator.')
pdf.step(4, 'The Lamp button in the Scan panel defaults to ON.',
         'It toggles the lamp directly from the app and turns amber when the lamp is '
         'lit.  During a scan the lamp is controlled automatically — turned off for '
         'dark frames, on for flat and projection captures — and the button updates '
         'to reflect the current state.  The lamp is switched off automatically when '
         'the app exits.')

pdf.note(
    'On first run the app creates a config/ folder alongside oct_app.py and stores '
    'default settings there.  You can adjust defaults via '
    'File → Save current settings as defaults.'
)

pdf.note(
    'Live preview: when the Pi camera is reachable, the preview panel shows a real '
    'camera image refreshed every 2 seconds.  When the lamp is off the preview will '
    'be dark — this is expected.  If the Pi is not reachable the panel falls back to '
    'the built-in camera simulator.'
)

pdf.subsection('Camera settings')
pdf.body(
    'The camera runs with auto-exposure disabled.  A fixed shutter speed is essential '
    'because Beer-Lambert law requires A = -log(I / I0) to be computed from images '
    'captured under identical conditions — any exposure change between the flat frame '
    'and a projection frame, or between the pre- and post-irradiation sessions, would '
    'introduce a spurious ΔA signal unrelated to the dosimeter.'
)
pdf.body(
    'The current shutter speed (500 ms at analogue gain 1.0) was determined empirically '
    'for correct exposure with the installed lamp and optics.  If the lamp is replaced '
    'or the optical path changes, recalibrate the shutter speed by adjusting '
    'ExposureTime in camera_server.py until the flat-field image is well-exposed '
    '(pixel values in the range 180–230 on an 8-bit scale) without saturation.'
)

pdf.section('Scan parameters — what each setting controls')

pdf.body('These controls appear in the Scan panel (left column) and the '
         'Reconstruction panel (right column).')
pdf.ln(1)
pdf.set_font('Arial', 'B', 9)
pdf.set_text_color(*C_HEADER)
pdf.set_x(MARGIN)
pdf.cell(42, 5.5, 'Parameter')
pdf.cell(22, 5.5, 'Default')
pdf.cell(0,  5.5, 'Description', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.rule(C_RULE, 0.4)

params = [
    ('Step size (°)',       '2°',    'Angular increment between projections.  '
                                     '2° gives 180 projections over 360°, balancing '
                                     'scan time against reconstruction quality.'),
    ('OCT stack',           '3',     'Number of frames averaged per projection angle '
                                     'to reduce camera noise.'),
    ('Calib stack',         '3',     'Frames averaged for dark and flat calibration captures.'),
    ('Settle (ms)',         '300',   'Time the motor pauses after each step before '
                                     'the camera captures, allowing vibration to damp out.'),
    ('Crop centre X (px)',  '995',   'Horizontal pixel position of the axis of rotation.  '
                                     'Read-only: the app measures it from the scan, and '
                                     'Auto-detect (button in Reconstruction panel) '
                                     're-estimates it from the pre-scan projections.  '
                                     'Tick "Set manually" to override, only if '
                                     'auto-detection has failed.'),
    ('Crop top Y (px)',     '270',   'Top edge of the region of interest in the image.  '
                                     'Should be just above the dosimeter top.'),
    ('Crop extent (px)',    '700',   'Side length of the square crop window.  '
                                     'Must encompass the full dosimeter diameter.'),
    ('Sample top (px)',     '0',     'First row (within the crop) of the dosimeter '
                                     'sample region, used for depth dose extraction.'),
    ('Sample height (px)',  '450',   'Number of rows of the dosimeter included in the '
                                     'depth dose ROI.'),
    ('Beam diameter (mm)',  '10.0',  'Diameter of the beam delivered to this dosimeter.  '
                                     'Recorded with the reconstruction so the measured '
                                     'irradiated region can be compared against it.  It '
                                     'does not change how the beam is located.'),
]
for p in params:
    pdf.param_row(*p)
    pdf.ln(0.5)

pdf.warning(
    'Crop centre X must be accurate.  An off-centre axis produces blurred ring '
    'artefacts in the reconstruction, without otherwise looking wrong.  That is why '
    'the box is read-only: run Auto-detect after a pre-scan and verify the axis line '
    'overlies the rotation axis in the preview, rather than typing a value.'
)

pdf.note(
    'Selecting a scan restores the settings it was last reconstructed with, so '
    'repeating a reconstruction repeats it exactly.  If anything has since been '
    'changed, the status panel says so before reconstructing, and a change of axis '
    'asks for confirmation first.'
)

# ── Page 3: Pre-irradiation scan ─────────────────────────────────────────────
pdf.add_page()

pdf.section('Session 1 — Pre-irradiation scan')

pdf.body(
    'This session is performed before the dosimeter is irradiated.  It captures the '
    'baseline absorbance of the unirradiated dosimeter.  A new scan folder is created and '
    'named automatically using the current date and time.'
)

pdf.subsection('Preparation')
pdf.step(1, 'Remove the dosimeter from any storage solution and dry the exterior.')
pdf.step(2, 'Mount the dosimeter on the rotation stage, centred on the axis of rotation.')
pdf.step(3, 'Ensure the lamp housing and camera are aligned and the optical path is clear.')

pdf.subsection('In the app')
pdf.step(4, 'At the workflow dialog, select "Pre-irradiation scan".',
         'The app opens ready for a pre-scan session.')
pdf.step(5, 'Verify scan parameters in the Scan panel.',
         'Step size, stack size, and settle time are the most commonly adjusted.')
pdf.step(6, 'Click "CAPTURE PRE SCAN".',
         'The four-phase checklist activates.')

pdf.ln(1)
pdf.set_font('Arial', 'B', 9.5)
pdf.set_text_color(*C_SUBHEAD)
pdf.cell(0, 5.5, 'Phase sequence:', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.set_text_color(*C_TEXT)

phases_pre = [
    ('(1) Dark capture',
     'Lamp OFF — automatic.',
     'Remove the dosimeter from the beam path, then click "Capture dark".  '
     'The app turns the lamp off automatically before capturing dark frames '
     'to characterise read-out noise and hot pixels.'),
    ('(2) Flat capture',
     'Lamp ON — automatic.',
     'Ensure no sample is in the beam, then click "Capture flat".  '
     'The app turns the lamp on automatically.  '
     'The camera captures the bare beam profile (I0).'),
    ('(3) Pre-irradiation scan',
     'Lamp ON — automatic.',
     'Place the dosimeter on the rotation stage.  '
     'Click "Begin pre-scan".  The motor steps through 360° and a projection '
     'image is captured at each angle.  Do not disturb the setup.  '
     'The lamp remains on when the scan finishes so you can inspect the dosimeter.'),
    ('(4) Post-irradiation scan',
     'Skipped in this session.',
     'This phase is automatically skipped for a pre-scan session.'),
]
for phase, action, detail in phases_pre:
    pdf.set_fill_color(*C_STEP_BG)
    pdf.set_x(MARGIN)
    pdf.set_font('Arial', 'B', 9)
    pdf.set_text_color(*C_HEADER)
    pdf.cell(40, 5.5, phase, fill=True)
    pdf.set_font('Arial', 'B', 9)
    pdf.set_text_color(*C_SUBHEAD)
    pdf.cell(46, 5.5, action, fill=True)
    pdf.set_font('Arial', '', 9)
    pdf.set_text_color(*C_TEXT)
    pdf.multi_cell(CONTENT_W - 86, 5.5, detail, fill=True,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)

pdf.step(7, 'When the scan finishes, the axis is auto-detected from the projections.',
         'The crop centre X spinbox updates automatically.  Check that the vertical '
         'axis line in the preview overlies the physical rotation axis.')
pdf.step(8, 'Irradiate the dosimeter.',
         'Remove the dosimeter, deliver the planned dose, then store it undisturbed '
         'until the post-irradiation scan.  Note the scan folder name — you will '
         'select it in Session 2.')

pdf.note(
    'The app saves projections to scans/<name>/pre/ and calibration frames to '
    'scans/<name>/calibration/.  Do not rename or move this folder.'
)

# ── Page 4: Post-irradiation scan ────────────────────────────────────────────
pdf.add_page()

pdf.section('Session 2 — Post-irradiation scan')

pdf.body(
    'This session is performed after irradiation, ideally within the dosimeter\'s '
    'stable post-irradiation window (consult the dosimeter protocol for timing requirements).  '
    'The post-scan is stored alongside the pre-scan data in the same folder.  '
    'ΔA projections are computed automatically on completion.'
)

pdf.subsection('In the app')
pdf.step(1, 'At the workflow dialog, select "Post-irradiation scan".',
         'A scan selector appears.')
pdf.step(2, 'Select the pre-scan folder for this dosimeter from the list.',
         'Only scans with a completed pre/ folder are shown.')
pdf.step(3, 'Click "Continue".',
         'The app opens with scan mode set to Post-irradiation.')
pdf.step(4, 'Click "CAPTURE POST SCAN" and follow the same four-phase sequence.',
         'Phases (1) and (2) (dark and flat) are re-captured — fresh calibration frames '
         'correct for any lamp drift since the pre-scan.  '
         'Phase (3) is skipped.  Phase (4) captures the post-irradiation projections.  '
         'The lamp remains on after the scan completes.')
pdf.step(5, 'The app checks the dosimeter went back in the same orientation.',
         'It measures how far the dosimeter was turned between the two sessions and '
         'corrects for it automatically, so you do not have to do anything.  '
         'If it was turned by more than a few degrees a dialog tells you so, purely '
         'so you know to line it up better next time.  '
         'The measurement is saved to rotation_offset.json.')

pdf.warning(
    'One dialog means the scan may not be right: "Check the dosimeter position", '
    'which appears when the app cannot work out how the dosimeter was sitting.  '
    'The scan is still saved and processed, but if the dosimeter was turned when '
    'you put it back, the dose results may be wrong.  Re-scanning is not possible '
    'once the dosimeter has been irradiated, so take care mounting it.'
)

pdf.step(6, 'On completion, ΔA = A_post − A_pre is computed for every projection.',
         'Frames are paired by the rotation angle in the filename, so a pre and post '
         'scan that used different angle settings cannot be silently mismatched.  '
         'The log reports any unpaired angles.  Pixels too dark to measure reliably '
         '(below 10 counts in either frame) are masked and reported as a percentage.  '
         'Results are saved to scans/<name>/subtracted/ as 16-bit PNG files.')

pdf.warning(
    'Mount the dosimeter in the same orientation as the pre-scan.  '
    'Any rotation of the dosimeter about the vertical axis between sessions '
    'will degrade the reconstruction quality.'
)
pdf.note(
    'Tip: mark the top of the dosimeter with a small permanent marker dot '
    'before the pre-scan.  Use it to align the securing screws and the "front" '
    'of the mount consistently each time the dosimeter is inserted.'
)

pdf.note(
    'The flat-field term I0 cancels mathematically in ΔA = log(I_pre/I_post), '
    'so small lamp-intensity changes between sessions do not bias the result.  '
    'However, large changes (lamp replacement, optical realignment) should be avoided.'
)

# ── Page 5: Reconstruction ────────────────────────────────────────────────────
pdf.add_page()

pdf.section('Reconstruction')

pdf.body(
    'Reconstruction converts the ΔA projection set into a 3-D attenuation volume '
    'using filtered back-projection (FBP) with a Hann filter.  The volume is stored '
    'as a NumPy array and cached; re-running reconstruction with the same crop '
    'parameters reloads the cache unless "Force new volume" is checked.'
)

pdf.subsection('Steps')
pdf.step(1, 'In the Reconstruction panel (right column), select the scan.',
         'Only scans with a subtracted/ folder are listed.')
pdf.step(2, 'Verify the crop and sample parameters.',
         'If a previous reconstruction exists, its parameters are loaded automatically.  '
         'Adjust if needed — changing any crop spinbox will automatically tick '
         '"Force new volume".')
pdf.note(
    'A scan you have just captured needs nothing extra: the post-irradiation '
    'session already did the subtraction.  "Recompute ΔA", the button beside "RECONSTRUCT", is '
    'only for older scans, and the status panel tells you when one needs it.  It '
    'redoes the subtraction from the images already saved with the scan, so nothing '
    'is re-captured; it takes about half a minute.  Reconstruct again afterwards '
    'with "Force new volume" ticked, which the app sets for you.'
)

pdf.step(3, 'Click "RECONSTRUCT".',
         'Progress is shown in the progress bar.  FBP runs in parallel across CPU '
         'cores; a typical 700×700 crop takes 1–3 minutes depending on hardware.')
pdf.step(4, 'Review the depth dose plot.',
         'The relative dose vs. depth curve appears in the plot panel.  '
         'Use the Relative / Absolute (OD) toggle to switch the Y axis.  '
         'Hover the cursor over the plot to read off interpolated values.')
pdf.step(5, 'The dose centroid is detected automatically.',
         'The app finds the brightest 20 % of slices in the sample ROI, excludes the '
         'dosimeter edge, removes whatever is evenly spread about the rotation axis, and '
         'computes the weighted centroid of what is left.  Removing the even part matters: '
         'a dosimeter darkens all over as it ages, and on a scan taken seven weeks after '
         'its pre-scan that overall darkening was several times larger than the beam and '
         'hid it completely.  The status panel reports how wide the region found is and '
         'how round, next to the beam diameter you entered, and warns only if the region '
         'spans most of the dosimeter, which means nothing localised was found.  '
         'The centroid offset is logged to the status '
         'panel and recorded in recon_config.json as dose_centroid_x_mm and '
         'dose_centroid_z_mm.  '
         'X is the left/right displacement as seen in the camera image (negative = '
         'left of axis).  Z is the front/back displacement along the camera line of '
         'sight (negative = closer to the camera than the rotation axis).')
pdf.step(6, 'Review the sanity-check visualisation.',
         'Saved to scans/<name>/reconstruct/sanity_check.png.  '
         'See Section 7 for how to interpret it.')

pdf.subsection('Output files')
outputs = [
    ('reconstruct/attenuation_volume.npy',  'Cached 3-D FBP volume (float32, Y×Z×X).'),
    ('reconstruct/example_cropped.png',     'First projection after cropping — quick crop check.'),
    ('reconstruct/sanity_check.png',        '4-panel diagnostic figure (see Section 7).'),
    ('depth-dose/depth_dose.png',           'Depth dose plot (relative dose vs. mm).'),
    ('depth-dose/depth_dose.xlsx',          'Tabulated depth_mm, rel_dose, dose_signal, OD.'),
    ('depth-dose/recon_config.json',        'Exact reconstruction parameters and the auto-detected dose centroid offset (mm) from the axis of rotation.'),
    ('dose-profiles/',                      'Per-slice radial dose profiles (every ~5% of depth).'),
]
for fname, desc in outputs:
    pdf.set_x(MARGIN + 2)
    pdf.set_font('Arial', '', 8.5)
    pdf.set_text_color(*C_SUBHEAD)
    pdf.cell(78, 5.5, fname)
    pdf.set_font('Arial', '', 9)
    pdf.set_text_color(*C_TEXT)
    pdf.multi_cell(CONTENT_W - 80, 5.5, desc,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)

pdf.subsection('Spreadsheet columns — depth_dose.xlsx')
cols = [
    ('depth_mm',
     'Depth position within the sample region in millimetres, starting at 0 at the '
     'top of the sample ROI and increasing at 0.1 mm per slice.  This is the x-axis '
     'of the depth dose plot.'),
    ('optical_density_depth',
     'Raw mean ΔA (change in optical density) at each depth slice, averaged over the '
     'central dose ROI cylinder.  Direct output of the FBP reconstruction with no '
     'further processing; values are in OD units.'),
    ('dose_signal',
     'Baseline-corrected ΔA: the mean of the top and bottom ~25 % of the sample '
     'column is used as a background baseline and subtracted, then negative values '
     'are clipped to zero.  Removes any DC offset from residual non-dose absorption.  '
     'Shown when the "Absolute (OD)" toggle is selected in the plot.'),
    ('rel_dose',
     'dose_signal normalised to its own maximum, giving a 0–1 relative dose curve.  '
     'This is the default plot view and the most useful column for comparing profiles '
     'across different measurements or irradiation conditions.'),
]
for col, desc in cols:
    pdf.set_x(MARGIN + 2)
    pdf.set_font('Arial', 'B', 9)
    pdf.set_text_color(*C_SUBHEAD)
    pdf.cell(52, 5.5, col)
    pdf.set_font('Arial', '', 9)
    pdf.set_text_color(*C_TEXT)
    pdf.multi_cell(CONTENT_W - 54, 5.5, desc,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(0.5)

pdf.note(
    'The Excel file contains raw (unsmoothed) values.  The EMA smoothing shown '
    'in the depth dose plot is for display only and is not written to the spreadsheet.'
)

pdf.subsection('Spreadsheet columns — dose_profiles.xlsx')
pdf.body(
    'Two sheets: relative_dose and optical_density.  Both have the same layout: '
    'rows are lateral position in mm (pos_mm), columns are sampled depth slices '
    'labelled by their depth (e.g. 3.50mm).  Approximately 20 evenly-spaced depths '
    'are sampled, covering the full reconstructed volume.'
)

pdf.subsection('Scan folder directory structure')
dirs = [
    ('pre/',
     'Raw intensity projection PNGs captured before irradiation.  '
     'One file per angle, named img_NNNN_DDD_deg.png.'),
    ('post/',
     'Raw intensity projection PNGs captured after irradiation.  '
     'Same naming convention as pre/.'),
    ('subtracted/',
     'ΔA = A_post − A_pre projections encoded as 16-bit PNG in offset binary '
     '(value = (ΔA + 4) × 65535/8, covering −4 to +4 OD, so negative ΔA is kept).  '
     'encoding.json records the format.  Input to the FBP reconstruction.'),
    ('rotation_offset.json',
     'How far the dosimeter was turned between the pre and post sessions, measured '
     'and corrected automatically after the post scan.  delta_phi_deg is the angle '
     'applied; confident says whether the measurement could be trusted; '
     'residual_lateral_px should be near zero, and a larger value points at the '
     'stage, camera or lamp having moved rather than at the dosimeter.'),
    ('calibration/',
     'Dark and flat calibration frames (dark.npy, flat.npy) captured at scan time.  '
     'Bundled here so the correct calibration is always available alongside the '
     'projection data, regardless of when the scan is exported.'),
    ('reconstruct/',
     'attenuation_volume.npy — cached FBP volume (float32, depth × Z × X).  '
     'example_cropped.png — first projection after cropping, for a quick crop check.  '
     'sanity_check.png — 4-panel diagnostic figure.'),
    ('depth-dose/',
     'depth_dose.png — relative dose vs. depth plot.  '
     'depth_dose.xlsx — tabulated depth dose data (see columns above).  '
     'recon_config.json — all reconstruction parameters and the detected dose '
     'centroid offset from the axis of rotation.'),
    ('dose-profiles/',
     'Per-slice radial dose profile PNGs (~20 evenly-spaced depths).  '
     'dose_profiles.xlsx — raw profile data: two sheets (relative_dose, '
     'optical_density), rows = lateral position (mm), columns = depth slice.'),
]
for dname, desc in dirs:
    pdf.set_x(MARGIN + 2)
    pdf.set_font('Arial', 'B', 9)
    pdf.set_text_color(*C_SUBHEAD)
    pdf.cell(30, 5.5, dname)
    pdf.set_font('Arial', '', 9)
    pdf.set_text_color(*C_TEXT)
    pdf.multi_cell(CONTENT_W - 32, 5.5, desc,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(0.5)

# ── Page 6: Sanity check + Export + Tips ─────────────────────────────────────
pdf.add_page()

pdf.section('Interpreting the sanity-check figure')

panels = [
    ('Sinogram\n(top-left)',
     'One row of the projection data at the mid-sample depth, plotted as angle (°) '
     'vs. column (px).  In a good scan with real dose signal you will see sinusoidal '
     'arcs tracing how each absorbing feature sweeps across the detector.  '
     'A flat, featureless sinogram means the signal is below noise.  '
     'Fixed vertical bands indicate a stationary feature (e.g. container wall) that '
     'did not cancel in the ΔA subtraction.'),
    ('Axial slice\n(top-right)',
     'Horizontal cross-section (XZ plane) through the reconstructed volume at the '
     'mid-sample depth.  A well-aligned scan shows a roughly circular dose '
     'distribution centred on the axis.  A bright ring at the boundary is a '
     'container-wall artefact.  The small teal circle marks the 10 px ROI used '
     'for depth dose extraction.'),
    ('Sagittal slice\n(bottom-left)',
     'Vertical slice (YZ plane) through the centre of the volume.  Depth (mm) is on '
     'the vertical axis; lateral position (mm) is on the horizontal axis.  '
     'Teal dashed lines bound the sample ROI; the white dotted line marks the exact '
     'depth row shown in the sinogram panel; the vertical teal strip marks the '
     'lateral dose ROI.  A horizontal brightness gradient within the ROI is the '
     'depth dose you are trying to measure.'),
    ('Depth dose\n(bottom-right)',
     'Smoothed relative dose vs. depth curve for this reconstruction, included to '
     'allow direct comparison with the sagittal slice and confirm that the ROI '
     'boundaries capture the correct region.'),
]
for panel, desc in panels:
    pdf.set_fill_color(*C_STEP_BG)
    pdf.set_x(MARGIN)
    pdf.set_font('Arial', 'B', 9)
    pdf.set_text_color(*C_HEADER)
    pdf.multi_cell(32, 5.5, panel, fill=True,
                   new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_font('Arial', '', 9)
    pdf.set_text_color(*C_TEXT)
    x_after = MARGIN + 34
    pdf.set_x(x_after)
    pdf.multi_cell(CONTENT_W - 34, 5.5, desc, fill=False,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1.5)

pdf.section('Exporting a scan')

pdf.body(
    'Use File → Export scan (or the "Export" workflow) to copy a completed scan '
    'folder to a chosen destination (e.g. a USB drive).  The exported folder contains '
    'the full scan directory tree, including:'
)
export_items = [
    'Raw pre and post projection PNGs',
    'ΔA (subtracted) projection PNGs',
    'Calibration frames (dark.npy and flat.npy captured at scan time)',
    'scan_meta.json — acquisition parameters (step size, stack depth) plus the date '
    'and time of both the pre- and post-irradiation sessions, so the interval '
    'between irradiation and readout stays on record',
    'recon_config.json — reconstruction parameters',
    'Depth dose plot and Excel table',
    'Sanity-check visualisation',
]
for item in export_items:
    pdf.set_x(MARGIN + 4)
    pdf.set_font('Arial', '', 9.5)
    pdf.set_text_color(*C_TEXT)
    pdf.cell(4, 5.5, '•')
    pdf.multi_cell(CONTENT_W - 8, 5.5, item,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)

pdf.section('Troubleshooting')

tips = [
    ('Blurred or ring artefacts in the axial slice',
     'Crop centre X is incorrect.  Click Auto-detect in the Reconstruction panel '
     'after selecting the scan, or adjust manually until the axis line in the '
     'preview overlies the physical rotation axis.  Tick "Force new volume" before '
     'reconstructing.'),
    ('Depth dose is flat or all noise',
     'The ΔA signal is below the noise floor.  Check that the correct pre-scan was '
     'selected during the post-scan session.  Inspect example_cropped.png in the '
     'reconstruct/ folder to confirm projections contain visible contrast.'),
    ('"Force new volume" is ticked unexpectedly',
     'Any change to the crop spinboxes automatically ticks this box to prevent '
     'stale cached volumes.  This is intentional.'),
    ('Phase button does not become active',
     'The previous phase is still running or the worker is waiting.  Check the log '
     'panel (bottom right) for error messages.'),
    ('Axis auto-detect gives a wrong value',
     'Auto-detect looks for the nozzle holder — a dark vertical band in the averaged '
     'projections.  If the band contrast is below 5 % it returns the previous value '
     'unchanged.  Verify lamp alignment and ensure the nozzle is within the crop window.'),
    ('Excel export fails',
     'pandas is not installed in the active environment.  The depth dose data is '
     'still available in the depth_dose.xlsx file once pandas is available, or use '
     'the depth dose plot PNG as an alternative.'),
    ('Live preview is dark or shows the simulator animation',
     'If the lamp is off, the preview will be black — click the Lamp button to turn '
     'it on.  If the preview shows the animated simulator pattern rather than a real '
     'camera image, the app cannot reach the Pi camera server: check that the Pi is '
     'powered on, that "Real camera" is ticked, and that the Pi IP address was entered '
     'correctly at startup.'),
]
for problem, solution in tips:
    pdf.set_x(MARGIN)
    pdf.set_font('Arial', 'B', 9)
    pdf.set_text_color(*C_SUBHEAD)
    pdf.cell(0, 5.5, problem, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font('Arial', '', 9)
    pdf.set_text_color(*C_TEXT)
    pdf.set_x(MARGIN + 4)
    pdf.multi_cell(CONTENT_W - 4, 5.5, solution,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1.5)

out_path = '/Users/E119562/repos/Optical-CT/optical_ct_user_guide.pdf'
pdf.output(out_path)
print(f'Saved: {out_path}')
