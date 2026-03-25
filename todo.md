# Nosis — Open Items

No critical items remain. The following are enhancement opportunities:

1. Push SoC LUT count toward sub-1000 (currently ~5,000 optimized LUT4 cells; nextpnr packs to ~2,500 slices)
2. BRAM initialization flow: $readmemh path tracking works but no test exercises it end-to-end with a hex file
3. Post-synthesis simulation accuracy: cell models are simplified behavioral, not cycle-accurate with ECP5 timing
