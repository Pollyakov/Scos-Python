SCOS

0. SCOS Parameters (can’t change after SCOS starts)
Window size
Number of dark frames N1
Number of bright frames N2
Recording length in minutes (enable Inf - it’s the defualt) - default 5 min.
Normalization number of seconds ( if recording length is set to equal or less than 1 min set to “auto (pulsation lower level)”, otherwise the default is 5 seconds )
“Save Frames” check box

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
“Please turn off the Laser”
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
X default is 5. Where should we store this setting for your opinion? In setting file or additional manu?
End when “Stop SCOS” is pressed or when time’s up. I suppose that some limit should be set. Lets say to 4 hours (also in some setting file).
Then save the rBFi and <I> data into .mat file and the graph into figure file (in python format).
