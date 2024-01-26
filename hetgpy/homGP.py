import numpy as np
import warnings
from time import time
from scipy.linalg.lapack import dtrtri
from scipy import optimize
from hetgpy.covariance_functions import cov_gen, partial_cov_gen, euclidean_dist
from hetgpy.utils import fast_tUY2, rho_AN
from hetgpy.find_reps import find_reps
from hetgpy.auto_bounds import auto_bounds
from hetgpy.find_reps import find_reps
MACHINE_DOUBLE_EPS = np.sqrt(np.finfo(float).eps)

class homGP():
    def __init__(self):
        return
    def __getitem__(self, key):
        return self.__dict__[key]
    def __setitem__(self,item,value):
        self.__dict__[item] = value
    def get(self,key):
        return self.__dict__.get(key)
    
    def logLikHom(self,X0, Z0, Z, mult, theta, g, beta0 = None, covtype = "Gaussian", eps = MACHINE_DOUBLE_EPS, env = None):
        
            n = X0.shape[0]
            N = Z.shape[0]

            C = cov_gen(X1 = X0, theta = theta, type = covtype)
            self.C = C
            Ki = np.linalg.cholesky(C + np.diag(eps + g / mult) ).T
            ldetKi = - 2.0 * np.sum(np.log(np.diag(Ki)))
            # to mirror R's chol2inv: do the following:
            # expose dtrtri from lapack (for fast cholesky inversion of a triangular matrix)
            # use result to compute Ki (should match chol2inv)
            Ki = dtrtri(Ki)[0] #  -- equivalent of chol2inv -- see https://stackoverflow.com/questions/6042308/numpy-inverting-an-upper-triangular-matrix
            Ki = Ki @ Ki.T     #  -- equivalent of chol2inv
            
            self.Ki = Ki
            if beta0 is None:
                beta0 = Ki.sum(axis=1) @ Z0 / Ki.sum()
            self.beta0 = beta0

            psi_0 = (Z0 - beta0).T @ Ki @ (Z0 - beta0)
            psi = (1.0 / N) * ((((Z-beta0).T @ (Z-beta0) - ((Z0-beta0)*mult).T @ (Z0-beta0)) / g) + psi_0)
            loglik = (-N / 2.0) * np.log(2*np.pi) - (N / 2.0) * np.log(psi) + (1.0 / 2.0) * ldetKi - (N - n)/2.0 * np.log(g) - (1.0 / 2.0) * np.log(mult).sum() - (N / 2.0)
            #print('loglik: ', loglik,'\n')
            return loglik
        
    def dlogLikHom(self,X0, Z0, Z, mult, theta, g, beta0 = None, covtype = "Gaussian",
                        eps = MACHINE_DOUBLE_EPS, components = ("theta", "g")):
        k = len(Z)
        n = X0.shape[0]
        
        C     = self.C # assumes these have been instantiated by a call to `logLikHom` first
        Ki    = self.Ki
        beta0 = self.beta0
        
        Z0 = Z0 - beta0
        Z  = Z - beta0

        KiZ0 = Ki @ Z0 ## to avoid recomputing  
        psi  = Z0.T @ KiZ0
        tmp1 = tmp2 = None

        # First component, derivative with respect to theta
        if "theta" in components:
            tmp1 = np.repeat(np.nan, len(theta))
            if len(theta)==1:
                dC_dthetak = partial_cov_gen(X1 = X0, theta = theta, type = covtype, arg = "theta_k") * C
                tmp1 = k/2 * (KiZ0.T @ dC_dthetak) @ KiZ0 /(((Z.T @ Z) - (Z0 * mult).T @ Z0)/g + psi) - 1/2 * np.trace(Ki @ dC_dthetak) # replaces trace_sym
                tmp1 = np.array(tmp1).squeeze()
            else:
                for i in range(len(theta)):
                    # use i:i+1 to preserve vector structure -- see "An integer, i, returns the same values as i:i+1 except the dimensionality of the returned self is reduced by 1"
                    ## at: https://numpy.org/doc/stable/user/basics.indexing.html
                    # tmp1[i] <- k/2 * crossprod(KiZ0, dC_dthetak) %*% KiZ0 /((crossprod(Z) - crossprod(Z0 * mult, Z0))/g + psi) - 1/2 * trace_sym(Ki, dC_dthetak)
                    dC_dthetak = partial_cov_gen(X1 = X0[:,i:i+1], theta = theta[i], type = covtype, arg = "theta_k") * C
                    tmp1[i] = (k/2 * (KiZ0.T @ dC_dthetak) @ KiZ0 /(((Z.T @ Z) - (Z0 * mult).T @ Z0)/g + psi) - 1/2 * np.trace(Ki @ dC_dthetak)).squeeze() # replaces trace_sym
        # Second component derivative with respect to g
        if "g" in components:
            tmp2 = k/2 * ((Z.T @ Z - (Z0 * mult).T @ Z0)/g**2 + np.sum(KiZ0**2/mult)) / ((Z.T @ Z - (Z0 * mult).T @ Z0)/g + psi) - (k - n)/ (2*g) - 1/2 * np.sum(np.diag(Ki)/mult)
            tmp2 = np.array(tmp2).squeeze()
        
        out = np.hstack((tmp1, tmp2)).squeeze()
        out = out[~(out==None)].astype(float).reshape(-1,1)
        #print('dll', out, '\n')
        return out

    def mleHomGP(self,X, Z, lower = None, upper = None, known = dict(),
                        noiseControl = dict(g_bounds = (MACHINE_DOUBLE_EPS, 1e2)),
                        init = {},
                        covtype = ("Gaussian", "Matern5_2", "Matern3_2"),
                        maxit = 100, eps = MACHINE_DOUBLE_EPS, settings = dict(returnKi = True, factr = 1e7)):
        known = known.copy()
        init = init.copy()
        if type(X) == dict:
            X0 = X['X0']
            Z0 = X['Z0']
            mult = X['mult']
            if sum(mult) != len(Z):    raise ValueError(f"Length(Z) should be equal to sum(mult): they are {len(Z)} \n and {sum(mult)}")
            if len(X0.shape) == 1:      warnings.warn(f"Coercing X0 to shape {len(X0)} x 1"); X0 = X0.reshape(-1,1)
            if len(Z0) != X0.shape[0]: raise ValueError("Dimension mismatch between Z0 and X0")
        else:
            if len(X.shape) == 1:    warnings.warn(f"Coercing X to shape {len(X)} x 1"); X = X.reshape(-1,1)
            if X.shape[0] != len(Z): raise ValueError("Dimension mismatch between Z and X")
            elem = find_reps(X, Z, return_Zlist = False)
            X0   = elem['X0']
            Z0   = elem['Z0']
            Z    = elem['Z']
            mult = elem['mult']

            
        covtypes = ("Gaussian", "Matern5_2", "Matern3_2")
        covtype = [c for c in covtypes if c==covtype][0]

        if lower is None or upper is None:
            auto_thetas = auto_bounds(X = X0, covtype = covtype)
            if lower is None: lower = auto_thetas['lower']
            if upper is None: upper = auto_thetas['upper']
            if known.get("theta") is None and init.get('theta') is None:  init['theta'] = np.sqrt(upper * lower)
        
        if len(lower) != len(upper): raise ValueError("upper and lower should have the same size")

        tic = time()

        if settings.get('return_Ki') is None: settings['return_Ki'] = True
        if noiseControl.get('g_bounds') is None: noiseControl['g_bounds'] = (MACHINE_DOUBLE_EPS, 1e2)
        
        g_min = noiseControl['g_bounds'][0]
        g_max = noiseControl['g_bounds'][1]

        beta0 = known.get('beta0')

        N = len(Z)
        n = X0.shape[0]

        if len(X0.shape) == 1: raise ValueError("X0 should be a matrix. \n")

        if known.get("theta") is None and init.get("theta") is None: init['theta'] = 0.9 * lower + 0.1 * upper # useful for mleHetGP
        
        
        if known.get('g') is None and init.get('g') is None: 
            if any(mult > 2):
                #t1 = mult.T
                #t2 = (Z.squeeze() - np.repeat(Z0,mult))**2
                init['g'] = np.mean(
                    (
                        (fast_tUY2(mult.T,(Z.squeeze() - np.repeat(Z0,mult))**2)/mult)[np.where(mult > 2)]
                    ))/np.var(Z0,ddof=1) 
            else:
                init['g'] = 0.1
        trendtype = 'OK'
        if beta0 is not None:
            trendtype = 'SK'
        
        ## General definition of fn and gr
        self.max_loglik = float('-inf')
        self.arg_max = None
        def fn(par, X0, Z0, Z, mult, beta0, theta, g):
            idx = 0 # to store the first non used element of par

            if theta is None: 
                theta = par[0:len(init['theta'])]
                idx   = idx + len(init['theta'])
            if g is None:
                g = par[idx]
            
            loglik = self.logLikHom(X0 = X0, Z0 = Z0, Z = Z, mult = mult, theta = theta, g = g, beta0 = beta0, covtype = covtype, eps = eps)
            
            if np.isnan(loglik) == False:
                if loglik > self.max_loglik:
                    self.max_loglik = loglik
                    self.arg_max = par
            
            return -1.0 * loglik # for maximization
        
        def gr(par,X0, Z0, Z, mult, beta0, theta, g):
            
            idx = 0
            components = []

            if theta is None:
                theta = par[0:len(init['theta'])]
                idx = idx + len(init['theta'])
                components.append('theta')
            if g is None:
                g = par[idx]
                components.append('g')
            dll = self.dlogLikHom(X0 = X0, Z0 = Z0, Z = Z, mult = mult, theta = theta, g = g, beta0 = beta0, covtype = covtype, eps = eps,
                            components = components)
            return -1.0 * dll # for maximization
        ## Both known
        if known.get('g') is not None and known.get("theta") is not None:
            theta_out = known["theta"]
            g_out = known['g']
            out = dict(value = self.logLikHom(X0 = X0, Z0 = Z0, Z = Z, mult = mult, theta = theta_out, g = g_out, beta0 = beta0, covtype = covtype, eps = eps),
                        message = "All hyperparameters given", counts = 0, time = time() - tic)
        else:
            parinit = lowerOpt = upperOpt = []
            if known.get("theta") is None:
                parinit = init['theta']
                lowerOpt = np.array(lower)
                upperOpt = np.array(upper)

            if known.get('g') is None:
                parinit = np.hstack((parinit,init.get('g')))
                lowerOpt = np.append(lowerOpt,g_min)
                upperOpt = np.append(upperOpt,g_max)
            bounds = [(l,u) for l,u in zip(lowerOpt,upperOpt)]
            out = optimize.minimize(
                fun=fn, # for maximization
                args = (X0, Z0, Z, mult, beta0, known.get('theta'), known.get('g')),
                x0 = parinit,
                jac=gr,
                method="L-BFGS-B",
                bounds = bounds,
                #tol=1e-8,
                options=dict(maxiter=maxit, #,
                            ftol = settings.get('factr',10) * np.finfo(float).eps,#,
                            gtol = settings.get('pgtol',0) # should map to pgtol
                            )
                )
            python_kws_2_R_kws = {
                'x':'par',
                'fun': 'value',
                'nit': 'counts'
            }
            for key, val in python_kws_2_R_kws.items():
                out[val] = out[key]
            if out.success == False:
                out = dict(par = self.arg_max, value = -1.0 * self.max_loglik, counts = np.nan,
                message = "Optimization stopped due to NAs, use best value so far")

            g_out = out['par'][-1] if known.get('g') is None else known.get('g')
            theta_out = out['par'][0:len(init['theta'])] if known.get('theta') is None else known['theta']
        
        ki = np.linalg.cholesky(
            cov_gen(X1 = X0, theta = theta_out, type = covtype) + np.diag(eps + g_out / mult)
            ).T
        ki = dtrtri(ki)[0]
        Ki = ki @ ki.T
        self.Ki = Ki
        if beta0 is None:
            beta0 = Ki.sum(axis=1) @ Z0 / Ki.sum()
        
        psi_0 = ((Z0 - beta0).T @ Ki) @ (Z0 - beta0)

        nu = (1.0 / N) * ((((Z-beta0).T @ (Z-beta0) - ((Z0-beta0)*mult).T @ (Z0-beta0)) / g_out) + psi_0)


        self.theta = theta_out
        self.g = g_out
        self.nu_hat = nu
        self.ll = -1.0 * out['value']
        self.nit_opt = out['counts']
        self.beta0 = beta0
        self.trendtype = trendtype
        self.covtype = covtype 
        self.msg = out['message'] 
        self.eps = eps
        self.X0 = X0
        self.Z0 = Z0 
        self.Z = Z
        self.mult = mult
        self.used_args = dict(lower = lower, upper = upper, known = known, noiseControl = noiseControl)
        self.time = time() - tic
        
        if settings["return_Ki"]: self.Ki  = Ki
        return self
    def predict(self, x, xprime = None):

        if len(x.shape) == 1:
            x = x.reshape(-1,1)
            if x.shape[1] != self['X0'].shape[1]: raise ValueError("x is not a matrix")
        if xprime is not None and len(xprime.shape)==1:
            xprime = xprime.reshape(-1,1)
            if xprime.shape[1] != self['X0'].shape[1]: raise ValueError("xprime is not a matrix")
        
        if self.get('Ki') is None:
            # these should be replaced with calls to self instead of self
            ki = np.linalg.cholesky(
            cov_gen(X1 = self['X0'], theta = self['theta'], type = self['covtype']) + np.diag(self['eps'] + self['g'] / self['mult'])
            ).T
            ki = dtrtri(ki)[0]
            self['Ki'] = ki @ ki.T
        self['Ki'] /= self['nu_hat'] # this is a subtle difference between R and Python. 
        kx = self['nu_hat'] * cov_gen(X1 = x, X2 = self['X0'], theta = self['theta'], type = self['covtype'])
        nugs = np.repeat(self['nu_hat'] * self['g'], x.shape[0])
        mean = self['beta0'] + kx @ (self['Ki'] @ (self['Z0'] - self['beta0']))
        
        if self['trendtype'] == 'SK':
            sd2 = self['nu_hat'] - np.diag(kx @ (self['Ki'] @ kx.T))
        else:
            sd2 = self['nu_hat'] - np.diag(kx @ ((self['Ki'] @ kx.T))) + (1- (self['Ki'].sum(axis=0))@ kx.T)**2/self['Ki'].sum()
        
        if (sd2<0).any():
            sd2[sd2<0] = 0
            warnings.warn("Numerical errors caused some negative predictive variances to be thresholded to zero. Consider using ginv via rebuild.homGP")

        if xprime is not None:
            kxprime = self['nu_hat'] * cov_gen(X1 = self['X0'], X2 = xprime, theta = self['theta'], type = self['covtype'])
            if self['trendtype'] == 'SK':
                if x.shape[0] < xprime.shape[0]:
                    cov = self['nu_hat'] *  cov_gen(X1 = self['X0'], X2 = xprime, theta = self['theta'], type = self['covtype']) - kx @ self['Ki'] @ kxprime
                else:
                    cov = self['nu_hat'] *  cov_gen(X1 = self['X0'], X2 = xprime, theta = self['theta'], type = self['covtype']) - kx @ (self['Ki'] @ kxprime)
            else:
                if x.shape[0] < xprime.shape[0]:
                    cov = self['nu_hat'] *  cov_gen(X1 = self['X0'], X2 = xprime, theta = self['theta'], type = self['covtype']) - kx @ self['Ki'] @ kxprime + ((1-(self['Ki'].sum(axis=0)).T @ kx).T @ (1-self['Ki'].sum(axis=0) @ kxprime))/self['Ki'].sum() #crossprod(1 - tcrossprod(rowSums(self$Ki), kx), 1 - rowSums(self$Ki) %*% kxprime)/sum(self$Ki)
        else:
            cov = None
        

        # re-modify self so Ki is preserved (because R does not modify lists in place)
        self['Ki']*=self['nu_hat']
        return dict(mean = mean, sd2 = sd2, nugs = nugs, cov = cov)

    def rebuild_homGP(self, robust = False):
        if robust :
            self['Ki'] <- np.linalg.pinv(
                cov_gen(X1 = self['X0'], theta = self['theta'], type = self['covtype']) + np.diag(self['eps'] + self['g'] / self['mult'])
            ).T
            self['Ki'] /= self['nu_hat']
        else:
            ki = np.linalg.cholesky(
            cov_gen(X1 = self['X0'], theta = self['theta'], type = self['covtype']) + np.diag(self['eps'] + self['g'] / self['mult'])
            ).T
            ki = dtrtri(ki)[0]
            self['Ki'] = ki @ ki.T
        return self

    def strip(self):
        keys  = ('Ki','Kgi','modHom','modNugs')
        for key in keys:
            if key in self.keys():
                del self[key]
        return self

class homTP():
    pass