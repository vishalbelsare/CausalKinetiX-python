import numpy as np
from scipy.interpolate import  BSpline
from matplotlib import pyplot as plt
from utils import get_BSbasis, get_basis_matrix, get_deriv_matrix, get_penalty_matrix
import numbers
from quadprog import solve_qp
from scipy.optimize import minimize_scalar 

def constrained_smoothspline(y,
                             times,
                             times_new,
                             pen_degree,
                             constraint="fixed",
                             derivative_values=None,
                             initial_value=None,
                             num_folds="leave-one-out",
                             lambd="optim"):
    # this function returns
    #{"smooth_vals"    :smoothed_spline_values,
    # "residuals"      :residuals,
    # "smooth_vals_new":predict_values,
    # "smooth_deriv"   :smooth_deriv,
    # "pen_par"        :lambd}
    
    
    ## Reorder data and deal with repetitions
    times, idx, cnt = np.unique(times, return_inverse=True, return_counts=True)# automatically sorted
    y_mean = np.zeros(len(times))
    derivative_values_mean = np.zeros(len(times))
    for i in range(len(y)):
        y_mean[idx[i]] += y[i]/cnt[idx[i]]
        derivative_values_mean += derivative_values[i]/cnt[idx[i]]
    y = y_mean
    derivative_values = derivative_values_mean

    ## Initialize some variables
    order_splines = pen_degree+2

    ## Construct folds for CV
    if num_folds=="leave-one-out":
        folds = {}
        for i in range(len(times)):
            folds[i] = i
        num_folds = len(folds)
    elif(isinstance(num_folds, int)):
        if(num_folds>1):
            folds = {f:[] for f in range(num_folds)}
            for i in range(len(times)): 
                folds[i%num_folds].append(i) 
        else:
            raise Exception("num_folds should be at least 2")
    else:
        raise Exception("num_folds was specified incorrectly")

    ## Function to ensure matrix is positive definite
    def make_posdef(A, mineig=10**(-10)):
        aa = np.linalg.eigvalsh(A)
        if min(aa) < mineig:
            #print("Spline matrix is not (numerically) positive definite and has been adjusted.")
            if min(aa) < 0:
                A = A + np.diag([-min(aa) + mineig]*A.shape[0])
            else:
                A = A + np.diag([mineig]*A.shape[0])
        return A

    ##############################
    # 
    # Step 1: Set up spline basis for CV
    #
    ##############################

    # intialize basis
    basis = get_BSbasis(data=times)
    penmat = get_penalty_matrix(basis)

    # CV variables
    if not isinstance(lambd, numbers.Number):
        meq = {}
        dvec = {}
        Amat = {}
        bvec = {}
        Dmat_1 = {}
        Bmat_val = {}
        validation = {}
        # compute the basis variables for each fold
        for i in range(num_folds):
            train = np.delete(np.arange(len(times)),folds[i])
            validation[i] = folds[i]
            train_y = y[train]
            Bmat = get_basis_matrix(times[train], basis)
            Bmat_val[i] = get_basis_matrix(times[validation[i]], basis)
            Bmat_deriv = get_basis_matrix(times[train], basis)
            dvec[i] = train_y @ Bmat
            Dmat_1[i] = Bmat.T @ Bmat
            # define QP-variables according to constraint
            if constraint=="fixed":
                meq[i] = len(train)
                Amat[i] = Bmat_deriv.T
                bvec[i] = derivative_values[train]
            elif constraint=="bounded":
                meq[i] = 0
                Amat[i] = np.concatenate([Bmat_deriv,-Bmat_deriv]).T
                bvec[i] = np.concatenate([derivative_values[train,1],-derivative_values[train,2]])
            elif constraint=="none":
                meq[i] = 0
                Amat[i] = np.zeros_like(Bmat_deriv.T)
                bvec[i] = np.zeros([len(train)])
            else:
                raise(Exception("Specified constraint does not exist. Use either fixed of bounded."))    
    if constraint=="none":
        sol_index = 3
    else:
        sol_index = 1

    ##############################
    # 
    # Step 2: Define cost function for optimization of penalty lambda
    #
    ##############################

    # Initialize upper and lower bound for penalty (see 5.4.1 in Functional Data Analysis)
    Bmat = get_basis_matrix(times, basis)
    BBnorm = np.trace(Bmat.T@Bmat)
    Rnorm = np.trace(penmat)
    r = BBnorm/Rnorm
    lower_spar = 0
    upper_spar = 5
    lower_lambd = r*256**(3*lower_spar-1)
    upper_lambd = r*256**(3*upper_spar-1)

    def cost_function(spar):
        lambd = r*256**(3*spar-1)
        Dmat_2 = lambd * penmat
        rss = 0
        for i in range(num_folds):
            Dmat = Dmat_1[i] + Dmat_2
            sc = np.linalg.norm(Dmat)#Frobenious norm
            Dmatsc = Dmat/sc
            # make sure that Dmatsc is pos.def
            Dmatsc = make_posdef(Dmatsc)
            # solve quadratic program with equality constraints
            solutions_qp = solve_qp(Dmatsc,
                                   dvec[i]/sc,
                                   Amat[i],
                                   bvec[i],
                                   meq[i])
            csol = solutions_qp[0]
            # compute validation error
            rss = rss + np.sum((y[validation[i]]-Bmat_val[i]@csol)**2)
        return(rss/num_folds)

    ##############################
    # 
    # Step 3: Compute lambda
    #
    ##############################

    if lambd=="optim":
        solutions_optim = minimize_scalar(fun=cost_function, bracket=[lower_spar, upper_spar], method="golden")
        spar = solutions_optim["x"]
        lambd = r*256**(3*spar-1)
    elif(lambd=="grid.search"):
        #lambda.vec = 10**(seq(log(lower.lambda)/log(10),log(upper.lambda)/log(10), by = 0.05))
        spar_vec = np.linspace(lower_spar, upper_spar, 100)
        RSS_vec = np.zeros([len(spar_vec)])
        for kkk in range(len(spar_vec)):
            RSS_vec[kkk] = cost_function(spar_vec[kkk])
        index_best_spar = len(RSS_vec)-1
        best_RSS = RSS_vec[index_best_spar]
        for j in reversed(range(index_best_spar-1)):
            if(RSS_vec[j] < 0.9*best_RSS):
                index_best_spar = j
                best_RSS = RSS_vec[index_best_spar]
        spar = spar_vec[index_best_spar]
        lambd = r*256**(3*spar-1)
    elif isinstance(lambd, numbers.Number):
        lambd = lambd
    else:
        raise(Exception("specified lambda is not a valid parameter or method"))

    # Check whether lambda is attained at boundaries
    if np.abs(lambd - lower_lambd) < 1e-16:
        raise(warning("There was at least one case in which CV yields a lambda at the lower boundary."))
    if np.abs(lambd - upper_lambd) < 1e-16:
        raise(warning("There was at least one case in which CV yields a lambda at the upper boundary."))

    ##############################
    # 
    # Step 4: Smoothing based on lambda
    #
    ##############################

    # set up quadratic program
    Bmat = get_basis_matrix(times, basis)
    Bmat_deriv = get_deriv_matrix(times, basis)
    dvec = (y @ Bmat).T
    Dmat_1 = Bmat.T @ Bmat
    Dmat_2 = lambd * penmat
    Dmat = Dmat_1 + Dmat_2
    sc = np.linalg.norm(Dmat)
    Dmatsc = Dmat/sc
    if constraint=="fixed":
        meq = len(times)
        Amat = Bmat_deriv.T
        bvec = derivative_values
    elif constraint=="bounded":
        meq = 0
        Amat = np.concatenate([Bmat_deriv,-Bmat_deriv]).T
        bvec = np.concatenate([derivative_values[:,1],-derivative_values[:,2]])
    elif constraint=="none":
        meq = 0
        Amat = np.zeros_like(Bmat_deriv.T)
        bvec = np.zeros([len(times)])

    # add initial value as constraint
    if not initial_value==None:
        meq = meq+1
        Amat = np.concatenate([Amat, np.append([1], np.zeros([Amat.shape[0]-1]))], axis=1)
        bvec = np.append(bvec, [initial_value])

    # make sure Dmatsc is pos.def.
    Dmatsc = make_posdef(Dmatsc)
    # solve quadratic program depending on constraint
    solutions_qp = solve_qp(Dmatsc,
                           dvec/sc,
                           Amat,
                           bvec,
                           meq)
    csol = solutions_qp[0]
    # smoothed values at times.new
    Bmat_new = get_basis_matrix(times_new, basis)
    predict_values = Bmat_new @ csol
    # smoothed values at times
    smoothed_spline_values = Bmat @ csol
    # smoothed derivative values
    smooth_deriv = Bmat_deriv @ csol
    # residuals
    residuals = (y-smoothed_spline_values)

return {"smooth_vals":smoothed_spline_values,
        "residuals":residuals,
        "smooth_vals_new":predict_values,
        "smooth_deriv":smooth_deriv,
        "pen_par":lambd}