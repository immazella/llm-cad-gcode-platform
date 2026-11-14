(Dogrulayici demosu: bu programda bilerek hatalar var)
G90
T1 M6
M3
G0 X0 Y0 Z5
G0 Z-2
G1 X20 Y0
G2 X40 Y0 I5 J0 F300
G1 X400 Y0
G83 X10 Y10 Z-8 R2 F100
G65 P9000
M30
G0 Z50
