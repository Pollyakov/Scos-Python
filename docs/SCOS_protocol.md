SCOS

*[Synced 2026-10-08 with Vika's current protocol text (pasted by the user). Lines in *[…]* are this repo's notes. Two places deliberately keep this repo's earlier correction instead of the pasted wording — the bright-calibration pop-up (laser **on**) and the refresh interval X — each explained where it occurs.]*

0. SCOS Parameters (can’t change after SCOS starts)
Window size

Number Of dark frames,( delete N1) Done
Number of bright frames, (delete N2 from the GUI) Done

Recording length in minutes (enable Infinity - it’s the default)  Done
If measurement duration changed to less than 1 minute - ask user “Change the Normalization type to “pulsation lower level”? “ Done

* Normalization type: Drop box with two options :
a) number of seconds
b) pulsation lower level Done

* Normalization number of frames:  field where the user can enter number of seconds.
(default = 5 ) Done

“Save Frames” check box Done

You can always click stop button - it stays enabled. Done


Get G[DU/e] conversion constant from table

Ask for recording name and location. Create appropriate folder.

Calibration 1: Dark Frames
Pop-up window that waits until the user clicks OK
“Please turn off the Laser”
Note: in the future I hope we could do that automatically
Set camera external trigger to OFF
Acquire N1 number of frames into a subfolder (look for the name format in matlab code). The number N1 should appear in SCOS parameters in the GUI. Default is 600. As you acquire the frames - calc mean and std for each pixel. (again, check the matlab code). Save the result into .mat file . Run mean spatial filter with appropriate window size. Save in a variables (var_dark, mean_dark) for later use.
Set external trigger to ON

5. Calibration 2: Bright Frames
(I hope to remove that requirement in the future and take values from an a-priory calibrated file )

Pop-up window that waits until the user clicks OK
“Please turn on the Laser”
*[Corrected 2026-10-04: this line originally read “turn off” — and Vika's text of 2026-10-08 still does. A bright calibration needs the laser on — with it off, N2 would be a second set of dark frames and var_bright would be wrong. Flagged by both code reviews (merged_worklist task 23).]*
*[Updated 2026-10-08: the bright frames are taken **with the subject in place** (user's instruction; it is also how the MATLAB reference's smoothingCoefficients.mat was made — from the recording itself). The app shows “Please turn on the laser. Keep the subject in the measurement area.” and says that the measurement starts automatically when this calibration ends. “Remove the subject” was never in this protocol; it had been added in the app.]*
Note: in the future I hope we could do that automatically
Acquire N2 number of frames into a subfolder (look for the name format in matlab code). The number N2 should appear in SCOS parameters in the GUI. Default is 600. As you acquire the frames - calc mean for each pixel and save the result into a .mat file. Save the result into .mat file . Run mean spatial filter with appropriate window size. Save in a variable (var_bright) for later use.

6. SCOS Calculation
*Before the loop: Decrease ROI radius by (window_size/2+1) pixels
In Loop:
Get frame. If required: save it as .tiff.
From each frame - subtract mean_dark image
Inside the ROI for each window: calc mean and spatial variance <I> , var_raw
Inside the ROI for each window: subtract camera noises, and divide by intensity
K2_fixed_curr =( var_raw - var_dark - var_bright - G*<I> -1/12 ) / <I>^2
BFi_curr = 1/K2_fixed

Add it to K2_fixed and BFi arrays (I suggest preallocating their size according to recording length. If it is infinite - extend by 10,000 point each time , cut at the end)

Do that for n first seconds to determine the normalization constant (mean_BFI_firstSeconds)
After that start presenting the graph (including the first seconds) of rBFi
rBFI = BFI/mean_BFI_firstSeconds.
Update the graph every X  seconds and update the image every X seconds. Make sure to stretch the x-axis accordingly every time the graph reaches the old limit.
X default is 1. Store the default in a settings file (e.g., config.yaml or settings.json) so it's easy to change without touching code.
Show it in the GUI's "Advanced settings" panel so a user can override it for one session.
*[Vika's text of 2026-10-08 still reads “X default is 5. Where should we store this setting for your opinion? In setting file or additional manu?”. That question was answered in this repo on 2026-05-05 (commit 341f24b) with the two lines above, and the app follows them: the plot refreshes every 1 s by design.]*
End when “Stop SCOS” is pressed or when time’s up. I suppose that some limit should be set. Lets say to 4 hours (also in some setting file).
Save  into .h5  file “rBFi_resultsAndParameters” : rBFi , <I> ,time vectors and all the parameters used. (TBD)
*[As implemented: the app writes `rBfi_results.h5` (not “rBFi_resultsAndParameters”) with `rBFi`, `Intensity` (<I>), `timeVec`, `startTime`, `Params`, plus `k2_raw`/`k2_corr`/`bfi` and a `metadata` group; the calibration goes to `Calibration.h5`.]*
Create a figure with two axes:  rBfi vs time  and <I> vs time. Add text box with all parameters used.  save into figure file (in python format). (TBD)
*[As implemented: `rBfi_fig.png` shows rBFi only. The <I> plot and the parameters box are todo U5; the reopenable “python format” figure is todo F5.]*


Link to Recording:    T2_short_40Hz

Notes:

Dark Calibration and Bright calibration happen automatically - no need for button -  Done

Add status (after info) indication about what is happening .  Done

Always leave the option to press Stop to abort the process even if it is in the middle of calibration. Done

Remove the “Save Data” button ( data is always saved).  Done
*[Not done in the app as of 2026-10-08: the “Save Data...” button is still on screen (`gui/main_window.py:371`), exporting κ² to .mat/.npz; the session data is saved automatically either way.]*

After “Start Scos” is pressed - > make the parameters input fields gray and change is forbidden.
When “Stop SCOS” is pressed -> the parameters are back on.  Done
