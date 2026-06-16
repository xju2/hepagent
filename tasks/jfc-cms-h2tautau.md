# Analysis Title: H→ττ Analysis with CMS Open Data (μτ_h final state)

You are performing a Higgs boson search in the τ+τ− decay channel using CMS Open Data from 2012 at √s = 8 TeV. The final state is one muon and one hadronically decaying tau lepton (μτ_h). Your goal is to produce distributions of key observables — particularly the visible di-tau mass — showing the Higgs signal contribution on top of Standard Model backgrounds. This loosely follows the official CMS publication (Phys. Lett. B 779 (2018) 283 and JHEP 05 (2014) 104). To optimize the higgs signal selection, do some categorization, in particular add a VBF category, be sure to fit all categories simulataneously. In addition our dataset is missing the full trigger and tau efficiency scale factors, loosen the tau efficiency selection to 10 to 15% to allow for a good good agreement of the drell-yan peak. In addition to a basline analysis, perform 3 more approaches that focus on fitting a different final observable these include a. perform the analysis fitting an fit NN discrimintor to find the higgs proppagating the systematic uncertainties in the fit b. train a NN to regress the direction of the genMET and genMET phi in the final state and use this to make a combined mass with the visible objects that you use as the final analysis, and finally c. fit the mass adding the missing energy to the mass distribution. Also, make sure to do a tight anti muon veto, and to normalize the W+jets sample from data using the high mT region. Finally, make sure you put a tight anti-muon veto on the hadronic tau id. Also make sure your Z normalization uncertainty is larger than the MC predictions, at a level of 10-15% to reflect missing trigger turn on scale factors and a larger tau efficiency scale factor.

## Data source
NanoAOD files hosted on the CERN Open Data portal. They are available at Perlmutter:
`/global/cfs/cdirs/m3443/data/cern-opendata/record_id_12350/CMS-Higgs2TauTau-OutReach/`.

The datasets include:
* GluGluToHToTauTau.root gg->H->tau-tau (mH=125 GeV) Signal
* VBF_HToTauTau.root VBF H->tau-tau Signal (subdominant)
* DYJetsToLL.root Z/gamma*->ll (Drell-Yan) Dominant irreducible background
* TTbar.root tt̄Background
* W1JetsToLNu.root, W2JetsToLNu.root, W3JetsToLNu.rootW+jets (1,2,3 jet bins) Background (fake τ_h)
* Run2012B_SingleMu.root, Run2012C_SingleMu.rootData (SingleMu trigger) Collision data
