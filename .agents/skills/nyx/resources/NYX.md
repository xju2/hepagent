# Nyx


## Tips and best practices
- The Nyx code is a cosmological simulation code that can run on CPUs and GPUs.
- When running on GPUs, configure `amr.nreaders` to match the number of GPUs.
- Set `nyx.particle_init_type = BinaryMetaFile`
- Look for initial condition files and put their full path to "FileList.txt"
- Set `nyx.binary_particle_file = FileList.txt`
